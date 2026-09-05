param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8503,

    [string]$AppDatabase
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$formalAppDatabase = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot "fomc_simulation.sqlite")
)
$displayAppDatabase = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot "fomc_simulation.decision_trace_50_display.sqlite")
)
$requiredFiles = @(
    "app.py",
    "fred_fomc_real.sqlite",
    "fomc_simulation.sqlite",
    "fomc_simulation.decision_trace_50_display.sqlite",
    "fomc_simulation.transcript_segmentation_v3_candidate.sqlite",
    "fixtures\next_meeting_official_context_2026-09-01.json",
    "model_spec\reaction_feature_contract_hackathon_r5_v1.json",
    "artifacts\reaction\pooled_ordered_logit_v1.json",
    "artifacts\reaction\fomc_2022_03_15_profile_cards_v1.json",
    "artifacts\evaluation\frozen_45_policy_baselines_v1.json",
    "artifacts\evaluation\statement_alert_audit_v1.json",
    "artifacts\evaluation\rate_only_censoring_audit_v1.json",
    "artifacts\forecast\fomc_2026_09_15_ensemble_v1\ensemble_forecast.json",
    "artifacts\forecast\fomc_2026_09_15_ensemble_v1\runs\naked_frozen_llm.json",
    "artifacts\forecast\fomc_2026_09_15_ensemble_v1\runs\named_persona_reaction.json",
    "artifacts\forecast\fomc_2026_09_15_ensemble_v1\runs\anonymous_persona_reaction.json",
    "artifacts\forecast\fomc_2026_09_15_ensemble_v1\runs\named_persona_no_reaction.json",
    "artifacts\codex_subscription\decision_trace_50_v5_atomic_monitor_segmentation_v3\qa_queue.json",
    "artifacts\cache\fomc_2022_03_15_offline_baseline.json"
)

foreach ($relativePath in $requiredFiles) {
    $resolvedPath = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $resolvedPath -PathType Leaf)) {
        throw "Required MVP artifact is missing: $resolvedPath"
    }
}

if ([string]::IsNullOrWhiteSpace($AppDatabase)) {
    $selectedAppDatabase = $displayAppDatabase
} else {
    $selectedAppDatabase = [System.IO.Path]::GetFullPath(
        (Join-Path $projectRoot $AppDatabase)
    )
    if (-not (Test-Path -LiteralPath $selectedAppDatabase -PathType Leaf)) {
        throw "App database does not exist: $selectedAppDatabase"
    }
}

$env:PYTHONUTF8 = "1"
$env:FOMC_APP_DB = $selectedAppDatabase
foreach ($proxyName in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")) {
    $processProxy = [Environment]::GetEnvironmentVariable($proxyName, "Process")
    $userProxy = [Environment]::GetEnvironmentVariable($proxyName, "User")
    if ($processProxy -and -not $userProxy) {
        try {
            $proxyHost = ([Uri]$processProxy).Host
        } catch {
            $proxyHost = ""
        }
        if ($proxyHost -in @("127.0.0.1", "localhost", "::1")) {
            Remove-Item "Env:$proxyName" -ErrorAction SilentlyContinue
        }
    }
}
python -m streamlit run (Join-Path $projectRoot "app.py") --server.headless true --server.address 127.0.0.1 --server.port $Port --browser.gatherUsageStats false
