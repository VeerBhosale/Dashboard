# Breaker+FVG Live Dashboard Setup

This dashboard can run live on GitHub Pages because it is a static HTML page backed by `breaker_fvg_dashboard_data.js`.

## One-time setup

1. Push this repository to GitHub.
2. Open the GitHub repository in your browser.
3. Go to `Settings` -> `Pages`.
4. Under `Build and deployment`, choose:
   - Source: `Deploy from a branch`
   - Branch: `main`
   - Folder: `/ (root)`
5. Save.

Your dashboard URL will look like:

```text
https://YOUR_USERNAME.github.io/YOUR_REPO/Newtest/Breaker_Based/
```

The folder `index.html` redirects to `breaker_fvg_dashboard.html`.

## Auto refresh

The workflow `.github/workflows/update-breaker-fvg-dashboard.yml` runs:

- manually from the GitHub `Actions` tab using `Run workflow`
- automatically Monday-Friday at `4:15 PM IST`

It downloads fresh Yahoo Finance data, regenerates `breaker_fvg_dashboard_data.js`, commits the updated file, and GitHub Pages serves the new dashboard.

## Notes

- The dashboard itself does not need a Python server.
- GitHub Actions runs the Python export script.
- If Yahoo Finance fails temporarily, rerun the workflow manually from the `Actions` tab.
