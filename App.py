import os
import requests as http_requests
import re
import logging
from datetime import datetime
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()  # Load .env file before reading any config

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
import PyPDF2
import docx
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIG  (Use .env in production!)
# ─────────────────────────────────────────────
class Config:
    SECRET_KEY          = os.environ.get('SECRET_KEY', 'change-me-in-production')
    UPLOAD_FOLDER       = os.environ.get('UPLOAD_FOLDER', 'uploads')
    MAX_CONTENT_LENGTH  = 5 * 1024 * 1024          # 5 MB upload limit
    ALLOWED_EXTENSIONS  = {'pdf', 'docx'}
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///database.db')
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Gemini AI — set GEMINI_API_KEY environment variable
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

    # Email — set these as environment variables; never commit plain-text passwords
    MAIL_SENDER   = os.environ.get('MAIL_SENDER',   'your_email@gmail.com')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD',  'your_app_password')
    MAIL_HOST     = os.environ.get('MAIL_HOST',      'smtp.gmail.com')
    MAIL_PORT     = int(os.environ.get('MAIL_PORT',  587))

    # Screening thresholds
    SCORE_HIGH   = 75.0   # auto-shortlist above this
    SCORE_MEDIUM = 50.0   # review above this

# ─────────────────────────────────────────────
# APP INIT
# ─────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)

db            = SQLAlchemy(app)
bcrypt        = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Ensure directories and DB are ready for production (Gunicorn)
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])


# Lazy-load heavy models once (avoids startup slowdown)
# Heavy local models removed to stay within Render Free Tier memory limits.
# We now use the Gemini API for all parsing and scoring.


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────
class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id       = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f'<User {self.username}>'


class Candidate(db.Model):
    __tablename__ = 'candidates'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(255))
    filename    = db.Column(db.String(255), nullable=False)
    email       = db.Column(db.String(255))
    phone       = db.Column(db.String(50))
    skills      = db.Column(db.Text)
    match_score = db.Column(db.Float)
    status      = db.Column(db.String(50), default='Pending')   # Pending / Shortlisted / Accepted / Rejected / Low Match
    applied_at  = db.Column(db.DateTime, default=datetime.utcnow)
    job_role    = db.Column(db.String(255))                      # Which role they applied for

    @property
    def score_label(self):
        """Human-readable tier based on match score."""
        if self.match_score is None:
            return 'Unknown'
        if self.match_score >= Config.SCORE_HIGH:
            return 'Strong'
        if self.match_score >= Config.SCORE_MEDIUM:
            return 'Medium'
        return 'Weak'

    def __repr__(self):
        return f'<Candidate {self.name} ({self.match_score}%)>'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Initialize database tables
with app.app_context():
    db.create_all()

# ─────────────────────────────────────────────
# HELPERS — FILE PARSING
# ─────────────────────────────────────────────
def allowed_file(filename: str) -> bool:
    return (
        '.' in filename
        and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']
    )


def extract_text_from_pdf(file_path: str) -> str:
    text = []
    try:
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text.append(extracted)
    except Exception as e:
        logger.warning("PDF extraction error for %s: %s", file_path, e)
    return " ".join(text)


def extract_text_from_docx(file_path: str) -> str:
    try:
        doc = docx.Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        logger.warning("DOCX extraction error for %s: %s", file_path, e)
        return ""


def extract_resume_text(file_path: str) -> str:
    """Route to the correct extractor based on file extension."""
    if file_path.lower().endswith('.pdf'):
        return extract_text_from_pdf(file_path)
    return extract_text_from_docx(file_path)


# ─────────────────────────────────────────────
# HELPERS — NLP / ANALYSIS
# ─────────────────────────────────────────────
SKILLS_DB = [
    'python', 'java', 'c++', 'javascript', 'typescript', 'html', 'css',
    'node.js', 'flask', 'django', 'fastapi', 'react', 'vue', 'angular',
    'mysql', 'postgresql', 'mongodb', 'sql', 'redis',
    'machine learning', 'deep learning', 'artificial intelligence',
    'tensorflow', 'pytorch', 'scikit-learn', 'keras',
    'llms', 'genai', 'langchain', 'hugging face',
    'git', 'docker', 'kubernetes', 'aws', 'azure', 'gcp',
    'data analysis', 'pandas', 'numpy', 'spark', 'tableau',
    'rest api', 'graphql', 'ci/cd', 'agile', 'linux',
]


def analyze_resume_with_gemini(resume_text: str, job_description: str) -> dict:
    """
    Use Gemini AI to parse the resume and match it against the job description.
    Returns a dictionary with name, email, phone, skills, match_score, and missing_skills.
    """
    api_key = app.config.get('GEMINI_API_KEY', '')
    if not api_key:
        logger.error("GEMINI_API_KEY not found during analysis.")
        return None

    prompt = f"""
    You are an expert HR Recruitment AI. Analyze the following resume text and compare it with the job description.
    
    RESUME TEXT:
    {resume_text}
    
    JOB DESCRIPTION:
    {job_description}
    
    Extract the following information and return it STRICTLY as a JSON object:
    {{
        "name": "Full name of candidate",
        "email": "Email address",
        "phone": "Phone number",
        "found_skills": ["List", "of", "skills", "found", "in", "resume"],
        "missing_skills": ["List", "of", "important", "skills", "from", "JD", "missing", "in", "resume"],
        "match_score": 85.5,
        "explanation": "Briefly explain why this score was given"
    }}
    
    Rules:
    - If Name/Email/Phone is not found, use "Not Found".
    - match_score should be a number between 0 and 100.
    - found_skills and missing_skills should be arrays of strings.
    - Return ONLY the JSON object. No other text.
    """

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        resp = http_requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        
        raw_text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
        
        # Strip potential markdown code blocks
        if raw_text.startswith("```json"):
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1].split("```")[0].strip()
            
        import json
        return json.loads(raw_text)
    except Exception as e:
        logger.error("Gemini analysis error: %s", e)
        return None

# ─────────────────────────────────────────────
# HELPERS — EMAIL
# ─────────────────────────────────────────────
def send_email(to_address: str, subject: str, body_html: str) -> bool:
    """Send an HTML email. Returns True on success, False on failure."""
    sender   = app.config['MAIL_SENDER']
    password = app.config['MAIL_PASSWORD']

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = sender
    msg['To']      = to_address
    msg.attach(MIMEText(body_html, 'html'))

    try:
        with smtplib.SMTP(app.config['MAIL_HOST'], app.config['MAIL_PORT']) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, to_address, msg.as_string())
        logger.info("Email sent to %s", to_address)
        return True
    except Exception as e:
        logger.error("Email failed to %s: %s", to_address, e)
        return False


def build_acceptance_email(name: str) -> str:
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;padding:24px">
      <h2 style="color:#2e7d32">Congratulations, {name}! 🎉</h2>
      <p>We are pleased to inform you that you have been <strong>shortlisted</strong>
         based on your resume evaluation.</p>
      <p>Our HR team will contact you within <strong>3–5 business days</strong>
         to schedule the next steps.</p>
      <br><p>Best regards,<br><strong>HR Team — RecruitAI</strong></p>
    </body></html>
    """


def build_rejection_email(name: str) -> str:
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;padding:24px">
      <h2 style="color:#c62828">Thank you for applying, {name}</h2>
      <p>After careful review, we regret to inform you that we will not be moving
         forward with your application at this time.</p>
      <p>We encourage you to apply for future openings that match your skills.</p>
      <br><p>Best regards,<br><strong>HR Team — RecruitAI</strong></p>
    </body></html>
    """


# ─────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            flash('Username and password are required.', 'danger')
            return redirect(url_for('register'))

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return redirect(url_for('register'))

        if User.query.filter_by(username=username).first():
            flash('Username already taken. Please choose another.', 'danger')
            return redirect(url_for('register'))

        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        db.session.add(User(username=username, password=hashed))
        db.session.commit()
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('hr_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user     = User.query.filter_by(username=username).first()

        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user, remember=request.form.get('remember') == 'on')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('hr_dashboard'))

        flash('Invalid username or password.', 'danger')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))


# ─────────────────────────────────────────────
# MAIN ROUTES
# ─────────────────────────────────────────────
@app.route('/')
def home():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_resume():
    job_description = request.form.get('job_description', '').strip()
    job_role        = request.form.get('job_role', 'General').strip()
    file            = request.files.get('resume')

    # ── Validate inputs ──────────────────────
    if not job_description:
        flash('Please provide a job description.', 'danger')
        return redirect(url_for('home'))

    if not file or file.filename == '':
        flash('No file selected.', 'danger')
        return redirect(url_for('home'))

    if not allowed_file(file.filename):
        flash('Only PDF and DOCX files are accepted.', 'danger')
        return redirect(url_for('home'))

    filename = secure_filename(file.filename)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    try:
        resume_text = extract_resume_text(filepath)

        if not resume_text.strip():
            flash('Could not extract text from the file. Please try a different file.', 'danger')
            return redirect(url_for('home'))

        # AI Analysis via Gemini (Lighter and Better)
        analysis = analyze_resume_with_gemini(resume_text, job_description)
        
        if analysis:
            name             = analysis.get('name', 'Unknown')
            email            = analysis.get('email', 'Not Found')
            phone            = analysis.get('phone', 'Not Found')
            found_skills     = analysis.get('found_skills', [])
            missing_skills   = analysis.get('missing_skills', [])
            match_percentage = analysis.get('match_score', 0.0)
        else:
            # Minimal fallback if Gemini fails
            name             = "Candidate"
            email            = "Not Found"
            phone            = "Not Found"
            found_skills     = []
            missing_skills   = []
            match_percentage = 0.0

        # Auto-determine initial status from score threshold
        if match_percentage >= Config.SCORE_HIGH:
            auto_status = 'Shortlisted'
        elif match_percentage >= Config.SCORE_MEDIUM:
            auto_status = 'Pending'
        else:
            auto_status = 'Low Match'

        new_candidate = Candidate(
            name=name,
            filename=filename,
            email=email,
            phone=phone,
            skills=", ".join(found_skills),
            match_score=match_percentage,
            status=auto_status,
            job_role=job_role,
        )
        db.session.add(new_candidate)
        db.session.commit()

        logger.info(
            "Processed resume: %s | Score: %.2f%% | Status: %s",
            name, match_percentage, auto_status
        )

        return render_template(
            'result.html',
            candidate=new_candidate,
            filename=filename,
            skills=found_skills,
            match_percentage=match_percentage,
            missing_skills=missing_skills,
            email=email,
            phone=phone,
            score_label=new_candidate.score_label,
        )

    except Exception as e:
        logger.exception("Error processing resume %s", filename)
        flash(f'Error processing resume: {str(e)}', 'danger')
        return redirect(url_for('home'))

    finally:
        # Always clean up the uploaded file to save disk space
        if os.path.exists(filepath):
            os.remove(filepath)


@app.route('/dashboard')
@login_required
def hr_dashboard():
    status_filter = request.args.get('status', '')
    search_query  = request.args.get('q', '').strip()
    page          = request.args.get('page', 1, type=int)

    query = Candidate.query

    if status_filter:
        query = query.filter_by(status=status_filter)

    if search_query:
        like = f'%{search_query}%'
        query = query.filter(
            Candidate.name.ilike(like)  |
            Candidate.email.ilike(like) |
            Candidate.skills.ilike(like)
        )

    candidates = query.order_by(Candidate.match_score.desc()).paginate(
        page=page, per_page=20, error_out=False
    )

    stats = {
        'total':       Candidate.query.count(),
        'shortlisted': Candidate.query.filter_by(status='Shortlisted').count(),
        'accepted':    Candidate.query.filter_by(status='Accepted').count(),
        'rejected':    Candidate.query.filter_by(status='Rejected').count(),
        'avg_score':   round(
            db.session.query(db.func.avg(Candidate.match_score)).scalar() or 0, 2
        ),
    }

    return render_template(
        'dashboard.html',
        candidates=candidates,
        stats=stats,
        status_filter=status_filter,
        search_query=search_query,
    )


@app.route('/candidate/<int:id>')
@login_required
def candidate_detail(id):
    candidate = Candidate.query.get_or_404(id)
    return render_template('candidate_detail.html', candidate=candidate)


@app.route('/delete/<int:id>', methods=['POST'])   # POST instead of GET — prevents accidental deletion via URL
@login_required
def delete_candidate(id):
    candidate = Candidate.query.get_or_404(id)
    name = candidate.name
    db.session.delete(candidate)
    db.session.commit()
    flash(f'Candidate "{name}" deleted.', 'info')
    return redirect(url_for('hr_dashboard'))


@app.route('/update-email/<int:id>', methods=['POST'])
@login_required
def update_email(id):
    """Allow HR to correct a candidate's email from the dashboard."""
    candidate = Candidate.query.get_or_404(id)
    new_email = request.form.get('email', '').strip()
    if new_email:
        candidate.email = new_email
        db.session.commit()
        flash(f'Email updated for {candidate.name}.', 'success')
    else:
        flash('Email cannot be empty.', 'danger')
    return redirect(url_for('hr_dashboard'))


@app.route('/accept/<int:id>', methods=['POST'])
@login_required
def accept_candidate(id):
    candidate = Candidate.query.get_or_404(id)

    # Use email override from form if provided (HR may have corrected it)
    email_to_use = request.form.get('email', '').strip() or candidate.email
    if email_to_use and email_to_use != candidate.email:
        candidate.email = email_to_use
        db.session.commit()

    if not candidate.email or candidate.email == "Not Found":
        flash('Candidate email not found — cannot send acceptance email.', 'danger')
        return redirect(url_for('hr_dashboard'))

    html    = build_acceptance_email(candidate.name)
    success = send_email(candidate.email, 'Job Offer — RecruitAI', html)

    candidate.status = 'Accepted'
    db.session.commit()

    if success:
        flash(f'{candidate.name} accepted and notified via email.', 'success')
    else:
        flash(f'{candidate.name} marked accepted but email failed to send.', 'warning')

    return redirect(url_for('hr_dashboard'))


@app.route('/reject/<int:id>', methods=['POST'])
@login_required
def reject_candidate(id):
    """Reject a candidate and optionally send them a polite notification."""
    candidate = Candidate.query.get_or_404(id)

    candidate.status = 'Rejected'
    db.session.commit()

    if candidate.email and candidate.email != "Not Found":
        html = build_rejection_email(candidate.name)
        send_email(candidate.email, 'Application Update — RecruitAI', html)

    flash(f'{candidate.name} has been rejected.', 'info')
    return redirect(url_for('hr_dashboard'))


# ─────────────────────────────────────────────
# JSON API  (for AJAX / future frontend use)
# ─────────────────────────────────────────────
@app.route('/api/stats')
@login_required
def api_stats():
    stats = {
        'total':       Candidate.query.count(),
        'shortlisted': Candidate.query.filter_by(status='Shortlisted').count(),
        'accepted':    Candidate.query.filter_by(status='Accepted').count(),
        'rejected':    Candidate.query.filter_by(status='Rejected').count(),
        'avg_score':   round(
            db.session.query(db.func.avg(Candidate.match_score)).scalar() or 0, 2
        ),
    }
    return jsonify(stats)


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """Proxy endpoint for the AI chatbot — calls Google Gemini securely from the server."""
    api_key = app.config.get('GEMINI_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'Gemini API key not configured. Set the GEMINI_API_KEY environment variable.'}), 500

    data = request.get_json(silent=True) or {}
    user_message = data.get('message', '').strip()
    context = data.get('context', '').strip()

    if not user_message:
        return jsonify({'error': 'Message is required.'}), 400

    # Build the prompt with resume context
    system_prompt = context if context else 'You are a helpful career assistant.'
    full_prompt = f"{system_prompt}\n\nUser question: {user_message}"

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}"
        payload = {
            "contents": [{
                "parts": [{"text": full_prompt}]
            }]
        }
        resp = http_requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        # Extract text from Gemini response
        ai_text = (
            result.get('candidates', [{}])[0]
            .get('content', {})
            .get('parts', [{}])[0]
            .get('text', 'Sorry, I could not generate a response.')
        )
        return jsonify({'reply': ai_text})

    except http_requests.exceptions.Timeout:
        logger.error("Gemini API timeout")
        return jsonify({'error': 'AI service timed out. Please try again.'}), 504
    except http_requests.exceptions.HTTPError as e:
        logger.error("Gemini API HTTP error: %s", e)
        return jsonify({'error': 'AI service returned an error. Check your API key.'}), 502
    except Exception as e:
        logger.exception("Gemini API error: %s", e)
        return jsonify({'error': 'Failed to get AI response. Please try again.'}), 500


# ─────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(413)
def file_too_large(e):
    flash('File too large. Maximum upload size is 5 MB.', 'danger')
    return redirect(url_for('home'))

@app.errorhandler(500)
def server_error(e):
    logger.exception("Internal server error")
    return render_template('500.html'), 500


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug_mode)