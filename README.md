# RecruitAI — Intelligent Resume Screening

RecruitAI is an AI-powered resume screening and tracking platform for HR professionals. It automates candidate evaluation using **Sentence Transformers** to match resumes against job descriptions (ATS scoring) and features an embedded **Google Gemini AI Chatbot** to provide candidates with instant career coaching and interview preparation.

## 🚀 Features

- **Semantic ATS Matching:** Uses BERT (`all-MiniLM-L6-v2`) to calculate true semantic similarity between a candidate's resume and the job description, rather than relying on basic keyword matching.
- **Automated Skill Extraction:** Uses `spaCy` NLP to identify and extract key technical skills from uploaded resumes.
- **Skill Gap Analysis:** Instantly highlights which required skills from the Job Description are missing from the resume.
- **Embedded AI Chatbot:** Features a contextual AI assistant powered by **Google Gemini 2.5 Flash Lite** to help candidates understand their score, bridge skill gaps, and prep for interviews.
- **HR Dashboard:** A secure, authenticated dashboard for recruiters to view, search, filter, and manage candidates in the pipeline.
- **Automated Email Notifications:** HR can accept or reject candidates with a single click, automatically sending tailored HTML emails via SMTP.

## 🛠️ Technology Stack

- **Backend:** Python, Flask, Flask-SQLAlchemy (SQLite), Flask-Login, Flask-Bcrypt
- **AI & NLP:** Google Gemini API, Sentence-Transformers, spaCy, PyPDF2, python-docx
- **Frontend:** HTML, CSS (Vanilla), Chart.js
- **Environment Management:** `python-dotenv`

## ⚙️ Setup Instructions

### 1. Clone the repository
```bash
git clone https://github.com/aman23-cmd/ai-resume-screening.git
cd ai-resume-screening
```

### 2. Install dependencies
Ensure you have Python 3.9+ installed.
```bash
pip install -r requirements.txt
```

*Note: You must also download the English language model for spaCy:*
```bash
python -m spacy download en_core_web_sm
```

### 3. Configure Environment Variables
Create a `.env` file in the root directory and add the following configuration:

```env
# Flask Settings
SECRET_KEY=change-me-in-production

# Gemini AI Chatbot (Required)
GEMINI_API_KEY=your-gemini-api-key

# Email Settings (Required for Accept/Reject functionality)
MAIL_SENDER=your_email@gmail.com
MAIL_PASSWORD=your_app_password
MAIL_HOST=smtp.gmail.com
MAIL_PORT=587

# Upload Directory
UPLOAD_FOLDER=uploads
```
> **Get a free Gemini API key here:** [Google AI Studio](https://aistudio.google.com/app/apikey)

### 4. Run the Application
```bash
python App.py
```
Open your browser and navigate to `http://127.0.0.1:5000`

## 🔒 Security Note
Never commit your `.env` file containing your API keys or email passwords. The `.gitignore` file is configured to exclude it.

## 👥 Team / Contributors

This project was developed as a group project by:
- **Aman Kaushal** ([aman23-cmd](https://github.com/aman23-cmd))
- **Paridhi Varshney** ([pari-alt](https://github.com/pari-alt)

## 📄 License
This project is open-source and available under the MIT License.
