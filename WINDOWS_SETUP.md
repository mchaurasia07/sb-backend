# Windows Setup

Use Python 3.12 for this project. Python 3.14 can force native builds for packages such as `pydantic-core` and `greenlet`, which requires Microsoft C++ Build Tools.

## Fix The Current Virtual Environment

From the project root:

```powershell
deactivate
Remove-Item -Recurse -Force .venv
```

Install Python 3.12 from `https://www.python.org/downloads/` if this command does not show a version:

```powershell
py -3.12 --version
```

Create a fresh Python 3.12 virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
```

The version must show Python `3.12.x`, not Python `3.14.x`.

Install dependencies:

```powershell
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

## Why This Happens

Your error log contains:

```text
Found CPython 3.14
error: Microsoft Visual C++ 14.0 or greater is required
error: linker `link.exe` not found
```

That means pip cannot find compatible prebuilt wheels for your Python version, so it tries to compile native extensions locally. Using Python 3.12 avoids that for the pinned dependency set.
