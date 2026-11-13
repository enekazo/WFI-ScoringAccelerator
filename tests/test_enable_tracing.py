import json
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def test_template_has_optional_tracing_names_and_no_secrets():
    p = json.loads((DEPLOY / "parameters.template.json").read_text(encoding="utf-8"))
    assert "appInsightsName" in p and "logAnalyticsName" in p
    assert "InstrumentationKey" not in json.dumps(p)


def test_enable_tracing_static_checks():
    text = (DEPLOY / "enable_tracing.ps1").read_text(encoding="utf-8")
    assert "AppInsights" in text and "SupportsShouldProcess" in text
    assert "InstrumentationKey=" not in text


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")), reason="no PowerShell")
def test_enable_tracing_whatif_makes_no_azure_calls():
    ps = shutil.which("pwsh") or shutil.which("powershell")
    r = subprocess.run([ps, "-NoProfile", "-File", str(DEPLOY / "enable_tracing.ps1"), "-WhatIf"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "log-analytics" in r.stdout and "app-insights" in r.stdout
