from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]

DEFAULT_TOP_LEVEL_FILES = (
    "app.py",
    "build_real_fred_db.py",
    "DATABASE_GUIDE.md",
    "DECISION_TRACE_HUMAN_REVIEW.md",
    "DEMO_SCRIPT.md",
    "fomc_calendar.py",
    "fomc_simulation.sqlite",
    "fomc_simulation.transcript_segmentation_v3_candidate.sqlite",
    "fomc_simulation.decision_trace_50_display.sqlite",
    "fomc_simulation.vote_labels_fixed_candidate.sqlite",
    "fred_fomc_demo.sql",
    "fred_fomc_real.sqlite",
    "fred_vintage_db.py",
    "HACKATHON_SUBMISSION.md",
    "R5_COMPLETION_AUDIT.md",
    "R5_TECHNICAL_COMPLETION_AUDIT_2026-09-01.md",
    "requirements.txt",
    "RUNBOOK.md",
    "run_app.ps1",
    "SUBMISSION_CHECKLIST.md",
    "SUBMISSION_RECORD.md",
    "sync_fomc_meetings.py",
    "artifacts/manifests/hackathon_r5_offline_build_2026-09-02_v33.json",
    "FOMC_決策記憶系統_Hackathon_MVP_開發計畫_R5.docx",
    "FOMC_決策記憶與_Self-Harness_混合架構_V3.2_正式開發規劃.docx",
)

DEFAULT_INCLUDE_DIRECTORIES = (
    "decision_memory",
    "docs",
    "document_manifests",
    "evaluation_spec",
    "fixtures",
    "metric_spec",
    "model_spec",
    "official_documents",
    "outcome_manifests",
    "schemas",
    "scripts",
    "submission_templates",
    "tests",
)

DEFAULT_REQUIRED_FILES = (
    "app.py",
    "run_app.ps1",
    "requirements.txt",
    "fred_fomc_real.sqlite",
    "fomc_simulation.sqlite",
    "fomc_simulation.transcript_segmentation_v3_candidate.sqlite",
    "fomc_simulation.decision_trace_50_display.sqlite",
    "fomc_simulation.vote_labels_fixed_candidate.sqlite",
    "RUNBOOK.md",
    "DATABASE_GUIDE.md",
    "R5_COMPLETION_AUDIT.md",
    "model_spec/reaction_feature_contract_hackathon_r5_v1.json",
    "artifacts/evaluation/r5_subscription_variant_matrix_v1.json",
    "artifacts/forecast/fomc_2026_09_15_ensemble_v1/ensemble_forecast.json",
    "artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/batch_status.json",
    "artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_sample_v1.json",
    "artifacts/codex_subscription/decision_trace_50_v5_atomic_monitor_segmentation_v3/human_review_results_v1.json",
    "artifacts/manifests/hackathon_r5_offline_build_2026-09-02_v33.json",
    "submission_templates/hackathon_r5_submission_signoff_v1.json",
)

METADATA_FILES = {
    "ENGINEERING_HANDOFF_zh-TW.md",
    "HANDOFF_MANIFEST.json",
    "SOURCE_FILES.txt",
    "SHA256SUMS.txt",
}

TEXT_EXTENSIONS = {
    ".cfg",
    ".csv",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sql",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}

SECRET_PATTERNS = (
    re.compile(r"sk-(?:proj|svcacct)-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:ghp|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

SUSPICIOUS_FILENAMES = {
    ".env",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "service_account.json",
}

KNOWN_SUBMISSION_BLOCKERS = (
    "submission_signoff",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_text(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _is_excluded(relative: Path) -> bool:
    lowered_parts = tuple(part.casefold() for part in relative.parts)
    name = relative.name.casefold()
    if lowered_parts[:2] == ("artifacts", "submission"):
        return True
    if any(
        part in {".git", ".codex", ".codex_doc_work", ".tmp", "__pycache__", "docx_qa"}
        or part.startswith("tmp")
        for part in lowered_parts[:-1]
    ):
        return True
    if name.startswith("~$") or relative.suffix.casefold() in {".pyc", ".pyo"}:
        return True
    if relative.suffix.casefold() == ".sqlite" and len(relative.parts) > 1:
        return True
    return False


def _assert_safe_path(path: Path) -> None:
    name = path.name.casefold()
    if (
        name in SUSPICIOUS_FILENAMES
        or name.startswith(".env.")
        or path.suffix.casefold() in {".key", ".p12", ".pem", ".pfx"}
    ):
        raise ValueError(f"secret-like filename is not allowed: {path.name}")


def _contains_secret_like_content(path: Path) -> bool:
    if path.suffix.casefold() not in TEXT_EXTENSIONS:
        return False
    carry = ""
    with path.open("r", encoding="utf-8", errors="ignore") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            candidate = carry + chunk
            if any(pattern.search(candidate) for pattern in SECRET_PATTERNS):
                return True
            carry = candidate[-256:]
    return False


def _collect_payload(
    root: Path,
    top_level_files: Sequence[str],
    include_directories: Sequence[str],
) -> list[Path]:
    collected: dict[str, Path] = {}
    for relative_text in top_level_files:
        source = (root / relative_text).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            raise FileNotFoundError(f"allowlisted file is missing: {relative_text}")
        relative = source.relative_to(root.resolve())
        if _is_excluded(relative):
            continue
        collected[relative.as_posix()] = source

    for relative_text in include_directories:
        directory = (root / relative_text).resolve()
        if not directory.is_relative_to(root.resolve()) or not directory.is_dir():
            raise FileNotFoundError(f"allowlisted directory is missing: {relative_text}")
        for source in directory.rglob("*"):
            if source.is_symlink():
                raise ValueError(f"symbolic links are not allowed: {source.name}")
            if not source.is_file():
                continue
            relative = source.resolve().relative_to(root.resolve())
            if _is_excluded(relative):
                continue
            collected[relative.as_posix()] = source.resolve()
    return [collected[key] for key in sorted(collected)]


def _collect_manifest_payload(root: Path, manifest_relative: str) -> list[Path]:
    resolved_root = root.resolve()
    manifest_path = (resolved_root / manifest_relative).resolve()
    if not manifest_path.is_relative_to(resolved_root) or not manifest_path.is_file():
        raise FileNotFoundError(f"artifact manifest is missing: {manifest_relative}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ValueError("artifact manifest files must be a list")
    paths: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("artifact manifest contains an invalid file entry")
        source = (resolved_root / entry["path"]).resolve()
        if not source.is_relative_to(resolved_root) or not source.is_file():
            raise FileNotFoundError(f"manifest artifact is missing: {entry['path']}")
        if source.stat().st_size != int(entry.get("byte_length", -1)):
            raise ValueError(f"manifest size mismatch: {entry['path']}")
        if _sha256(source) != entry.get("sha256"):
            raise ValueError(f"manifest hash mismatch: {entry['path']}")
        paths.append(source)
    return paths


def _write_source_files(output: Path, payload_paths: Sequence[str]) -> None:
    text = "".join(f"{path}\n" for path in payload_paths)
    (output / "SOURCE_FILES.txt").write_text(text, encoding="utf-8", newline="\n")


def _write_handoff_guide(
    output: Path,
    *,
    project_name: str,
    snapshot_date: str,
    source_database_hash: str | None,
    app_database_hash: str | None,
) -> None:
    guide = f"""# {project_name} 工程接手說明

## 交付狀態

- 快照日期：{snapshot_date}
- 類型：working-tree engineering snapshot（原專案沒有 Git repository/tag）
- 工程接手：READY
- 技術就緒：READY（11/11 technical checks 通過）
- Hackathon 正式投稿：PENDING_REAL_WORLD_SIGNOFF
- Production operational：否；這是離線研究 MVP，不是已部署 production release

## 快速啟動

```powershell
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\\run_app.ps1 -Port 8503
```

開啟 `http://localhost:8503`。核心預測與證據瀏覽不需要 API key；只有使用者按下 AI 統整按鈕才會呼叫 Responses API。請遵守 `RUNBOOK.md` 的邊界，只允許 Windows User-scope `OPENAI_API_KEY`，不要把金鑰放進本資料夾。

## 驗證

```powershell
python -m unittest discover -s tests -p "test_*.py"
python -m decision_memory.preflight
python -m decision_memory.submission_gate --scope technical
python -m decision_memory.submission_gate
python -m decision_memory.engineering_handoff verify --output .
```

technical gate 應回傳 `READY`。完整 submission gate 仍會因為尚未進行的真人投稿簽核回傳 `BLOCKED`，不影響工程接手。

## 資料邊界

- `fred_fomc_real.sqlite`：正式凍結的 FRED/FOMC point-in-time 來源庫；SHA-256 `{source_database_hash or 'not included'}`。
- `fomc_simulation.sqlite`：正式凍結的離線 app DB；SHA-256 `{app_database_hash or 'not included'}`。
- `fomc_simulation.decision_trace_50_display.sqlite`：51 場完整 replay、115 場基礎案例的唯讀展示庫；由 transcript v3 candidate 可重建。
- 正式 app DB 與 DecisionTrace 展示庫均為唯讀；FOMC-only 介面不提供任何資料庫寫入操作。
- 這兩個 SQLite 是研究 MVP 的正式資料，不是線上 production datastore；production API、權限、備份與部署仍須另行設計與驗證。

## 目前已完成

- 50/50 DecisionTrace 完成確定性重驗；凍結的 12-case 人工樣本由 Nik 完成 12/12 PASS。這只證明樣本 gate，不代表 50 場逐場人工審核。
- 五種 Frozen 45 subscription 變體共 225/225 case results。
- 八列評估矩陣、四個聯準會頁面、下一場會議四模型鎖定預測、三模式現行程式演練、v33 可攜式不可變清單、資料庫與操作文件均已納入。

## 之後正式投稿時才需要完成

1. `submission_signoff`：影片、三次計時演練、主辦方確認與第二人覆核後，由真人簽核。

## 工程師建議閱讀順序

1. `R5_TECHNICAL_COMPLETION_AUDIT_2026-09-01.md`
2. `RUNBOOK.md`
3. `DATABASE_GUIDE.md`
4. `docs/plans/2026-08-29-engineering-handoff-package.md`
5. `R5_COMPLETION_AUDIT.md`（歷史稽核）

## 完整性檔案

- `SOURCE_FILES.txt`：封裝 payload 清單。
- `HANDOFF_MANIFEST.json`：快照狀態、每個 payload 的大小與 SHA-256。
- `SHA256SUMS.txt`：全部 payload 與非循環 metadata 的 SHA-256。

可描述為「R5 technical MVP READY」。在真人 signoff 完成前，不要描述為正式投稿完成；也不要宣稱 API-equivalent promotion、完整 Self-Harness promotion 或 production deployment。
"""
    (output / "ENGINEERING_HANDOFF_zh-TW.md").write_text(
        guide, encoding="utf-8", newline="\n"
    )


def _write_checksums(output: Path) -> None:
    included = sorted(
        path
        for path in output.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    lines = [f"{_sha256(path)}  {_relative_text(path, output)}\n" for path in included]
    (output / "SHA256SUMS.txt").write_text(
        "".join(lines), encoding="utf-8", newline="\n"
    )


def build_engineering_handoff(
    root: Path,
    output: Path,
    *,
    top_level_files: Sequence[str] = DEFAULT_TOP_LEVEL_FILES,
    include_directories: Sequence[str] = DEFAULT_INCLUDE_DIRECTORIES,
    required_files: Sequence[str] = DEFAULT_REQUIRED_FILES,
    artifact_manifest: str | None = None,
    project_name: str = "FOMC Decision Memory R5",
    snapshot_date: str,
) -> dict[str, object]:
    root = root.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    if output == root:
        raise ValueError("output cannot be the source root")
    for include_directory in include_directories:
        include_root = (root / include_directory).resolve()
        if output.is_relative_to(include_root):
            raise ValueError("output cannot be inside an allowlisted directory")

    for required in required_files:
        required_path = (root / required).resolve()
        if not required_path.is_relative_to(root) or not required_path.is_file():
            raise FileNotFoundError(f"required handoff file is missing: {required}")

    payload_sources = _collect_payload(root, top_level_files, include_directories)
    if artifact_manifest is not None:
        merged = {
            source.relative_to(root).as_posix(): source for source in payload_sources
        }
        for source in _collect_manifest_payload(root, artifact_manifest):
            merged[source.relative_to(root).as_posix()] = source
        payload_sources = [merged[key] for key in sorted(merged)]
    secret_finding_count = 0
    for source in payload_sources:
        _assert_safe_path(source)
        if _contains_secret_like_content(source):
            secret_finding_count += 1
    if secret_finding_count:
        raise ValueError(
            f"secret-like content found in {secret_finding_count} allowlisted file(s)"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.building-{uuid.uuid4().hex}"
    if staging.exists():
        raise FileExistsError(f"staging path already exists: {staging}")
    try:
        staging.mkdir()
        payload_entries: list[dict[str, object]] = []
        for source in payload_sources:
            relative = source.relative_to(root)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            payload_entries.append(
                {
                    "path": relative.as_posix(),
                    "bytes": target.stat().st_size,
                    "sha256": _sha256(target),
                }
            )

        payload_paths = [str(entry["path"]) for entry in payload_entries]
        _write_source_files(staging, payload_paths)
        source_db = next(
            (entry for entry in payload_entries if entry["path"] == "fred_fomc_real.sqlite"),
            None,
        )
        app_db = next(
            (entry for entry in payload_entries if entry["path"] == "fomc_simulation.sqlite"),
            None,
        )
        _write_handoff_guide(
            staging,
            project_name=project_name,
            snapshot_date=snapshot_date,
            source_database_hash=str(source_db["sha256"]) if source_db else None,
            app_database_hash=str(app_db["sha256"]) if app_db else None,
        )
        manifest = {
            "schema_version": "engineering_handoff_v2",
            "project_name": project_name,
            "snapshot_date": snapshot_date,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_kind": "working_tree_engineering_snapshot",
            "source_control": "git_unavailable",
            "engineering_handoff_status": "READY",
            "technical_readiness_status": "READY",
            "hackathon_submission_status": "PENDING_REAL_WORLD_SIGNOFF",
            "production_operational": False,
            "offline_research_data_status": "verified_formal_sqlite_snapshot",
            "payload_file_count": len(payload_entries),
            "payload_bytes": sum(int(entry["bytes"]) for entry in payload_entries),
            "secret_scan": {
                "scope": "allowlisted filenames and text content",
                "high_confidence_finding_count": secret_finding_count,
                "binary_content_exhaustive": False,
            },
            "known_submission_blockers": list(KNOWN_SUBMISSION_BLOCKERS),
            "artifact_manifest": artifact_manifest,
            "excluded_categories": [
                "backup SQLite files",
                "temporary and cache directories",
                "Word lock files",
                "old superseded Word plans",
                "secret-like filenames or high-confidence text patterns",
            ],
            "files": payload_entries,
        }
        (staging / "HANDOFF_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        _write_checksums(staging)
        os.replace(staging, output)
    except BaseException:
        if staging.exists() and staging.parent == output.parent:
            shutil.rmtree(staging)
        raise

    verification = verify_engineering_handoff(output)
    if not verification["valid"]:
        raise RuntimeError("new handoff failed its own integrity verification")
    return {
        "output": str(output),
        "payload_file_count": len(payload_sources),
        "payload_bytes": manifest["payload_bytes"],
        "secret_finding_count": secret_finding_count,
        "verification": verification,
    }


def _read_checksum_file(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line:
            continue
        digest, relative = raw_line.split("  ", 1)
        checksums[relative] = digest
    return checksums


def verify_engineering_handoff(output: Path) -> dict[str, object]:
    output = output.resolve()
    missing_metadata = sorted(
        name for name in METADATA_FILES if not (output / name).is_file()
    )
    if missing_metadata:
        return {
            "valid": False,
            "missing_metadata": missing_metadata,
            "hash_mismatch_count": 0,
            "unexpected_file_count": 0,
            "ignored_runtime_file_count": 0,
            "secret_finding_count": 0,
        }

    manifest = json.loads((output / "HANDOFF_MANIFEST.json").read_text(encoding="utf-8"))
    manifest_files = {item["path"]: item for item in manifest["files"]}
    source_files = {
        line
        for line in (output / "SOURCE_FILES.txt").read_text(encoding="utf-8").splitlines()
        if line
    }
    all_actual_files = {
        _relative_text(path, output) for path in output.rglob("*") if path.is_file()
    }
    ignored_runtime_files = {
        relative for relative in all_actual_files if _is_excluded(Path(relative))
    }
    actual_files = all_actual_files - ignored_runtime_files
    payload_actual = actual_files - METADATA_FILES
    unexpected_files = sorted(payload_actual - source_files)
    missing_payload = sorted(source_files - payload_actual)
    manifest_list_mismatch = source_files != set(manifest_files)

    checksum_file = _read_checksum_file(output / "SHA256SUMS.txt")
    checksum_expected_paths = actual_files - {"SHA256SUMS.txt"}
    checksum_list_mismatch = set(checksum_file) != checksum_expected_paths
    hash_mismatches: list[str] = []
    for relative, expected in checksum_file.items():
        candidate = (output / relative).resolve()
        if not candidate.is_relative_to(output) or not candidate.is_file():
            hash_mismatches.append(relative)
            continue
        if _sha256(candidate) != expected:
            hash_mismatches.append(relative)
    for relative, entry in manifest_files.items():
        candidate = (output / relative).resolve()
        if not candidate.is_relative_to(output) or not candidate.is_file():
            if relative not in hash_mismatches:
                hash_mismatches.append(relative)
            continue
        if _sha256(candidate) != entry["sha256"] or candidate.stat().st_size != entry["bytes"]:
            if relative not in hash_mismatches:
                hash_mismatches.append(relative)

    secret_findings = 0
    for relative in sorted(payload_actual):
        candidate = output / relative
        try:
            _assert_safe_path(candidate)
        except ValueError:
            secret_findings += 1
            continue
        if _contains_secret_like_content(candidate):
            secret_findings += 1

    valid = not any(
        (
            unexpected_files,
            missing_payload,
            manifest_list_mismatch,
            checksum_list_mismatch,
            hash_mismatches,
            secret_findings,
        )
    )
    return {
        "valid": valid,
        "payload_file_count": len(payload_actual),
        "payload_bytes": sum((output / path).stat().st_size for path in payload_actual),
        "missing_metadata": [],
        "unexpected_file_count": len(unexpected_files),
        "missing_payload_count": len(missing_payload),
        "ignored_runtime_file_count": len(ignored_runtime_files),
        "manifest_list_mismatch": manifest_list_mismatch,
        "checksum_list_mismatch": checksum_list_mismatch,
        "hash_mismatch_count": len(hash_mismatches),
        "secret_finding_count": secret_findings,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or verify an engineering handoff")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--root", type=Path, default=ROOT)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--snapshot-date", required=True)
    build.add_argument(
        "--artifact-manifest",
        default="artifacts/manifests/hackathon_r5_offline_build_2026-09-02_v33.json",
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "build":
        result = build_engineering_handoff(
            args.root,
            args.output,
            snapshot_date=args.snapshot_date,
            artifact_manifest=args.artifact_manifest,
        )
    else:
        result = verify_engineering_handoff(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("valid", result.get("verification", {}).get("valid")) else 1


if __name__ == "__main__":
    sys.exit(main())
