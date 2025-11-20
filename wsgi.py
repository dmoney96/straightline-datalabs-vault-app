from scripts.web_app import app

# Gunicorn entrypoint: wsgi:app
if __name__ == "__main__":
    # Dev-only fallback; systemd + gunicorn won't hit this.
    app.run(host="127.0.0.1", port=5001, debug=True)
