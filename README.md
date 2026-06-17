# rate-my-professors

Kyrgyzstan-focused Rate My Professors analogue built with Django.

## Local setup

Use Python 3.12 or newer. On Windows, replace `python` with `py -3` or the full
path to your Python executable if the `python` alias is disabled.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py check
python manage.py migrate
python manage.py test
python manage.py runserver
```

Open http://127.0.0.1:8000/.

## Configuration

Development defaults work without environment variables. Production must set:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS=example.com,www.example.com`
