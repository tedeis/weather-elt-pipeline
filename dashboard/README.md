# Dashboard

Run locally:

```bash
streamlit run dashboard/app.py
```

## Deploying for a live demo link (free)

1. Push this repo to GitHub (make sure `data/weather.duckdb` is committed —
   the GitHub Actions workflow keeps it updated daily).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with
   GitHub, and create a new app pointing at this repo with main file path
   `dashboard/app.py`.
3. Streamlit Community Cloud will install `requirements.txt` and deploy
   automatically. You'll get a public URL to put in your resume/portfolio.
