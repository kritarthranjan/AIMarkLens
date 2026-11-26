# Contributing

Thanks for helping improve AIMarkLens. Bug reports, documentation fixes, and focused code changes are welcome.

## Set up the project

AIMarkLens is developed with Python 3.12 on Windows 10 or 11.

```powershell
git clone https://github.com/kritarthranjan/AIMarkLens
cd AIMarkLens
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run the test suite before submitting a change:

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Tests use local mocks and do not contact real online services.

## Automated security checks

GitHub Actions runs CodeQL on the Python code and Gitleaks to check for exposed secrets, including API keys.
Resolve findings before merging. These checks do not replace reviewing code or configuring repository rules to
require passing checks.

## Make a change

- Keep changes focused and explain the problem they solve.
- Add or update tests for behavior changes.
- Keep analysis engines independent of Qt; the UI belongs in `AIMarkLens/ui`.
- Preserve the read-only handling of original files and the existing privacy safeguards.
- Do not add network uploads or telemetry without clear user consent and documentation.
- Avoid including API keys, private files, or other sensitive data in commits, issues, or screenshots.
- For UI changes, include a screenshot or a short description of the visible change.

## Submit your contribution

Open a pull request with a clear summary, relevant test results, and any user-facing changes. For security
vulnerabilities, do not open a public issue; follow [SECURITY.md](SECURITY.md) instead.

