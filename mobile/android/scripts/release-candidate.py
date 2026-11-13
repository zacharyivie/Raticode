#!/usr/bin/env python3
"""Check, stage and publish Raticode Mobile release candidates without rebuilding.

This follows the desktop's scripts/release-candidate.py. A push to stage builds and
tests a release APK, then stages it with its evidence as a draft GitHub release. A
mobile-v<versionName> tag publishes that exact draft after verifying it; the tag job
never builds. check-apk, audit and device-test are the Android checks, and
scripts/build-release.sh runs check-apk locally. The build, lint, release policy,
dependency audit and the release APK's launch on an emulator must pass; the
instrumented emulator tests are recorded and reported but don't block a candidate.
See docs/releasing.md.
All subprocess arguments and API payloads are passed without shell interpolation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import version as app_version  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "scripts/release-policy.json"
MANIFEST = "candidate-manifest.json"
WORKFLOW = ".github/workflows/mobile-release-candidate.yml"
GROUPS = ("android", "audit-evidence")
ARTIFACT_PREFIX = "raticode-mobile-"
MAX_AGE = timedelta(days=7)
READY = "<!-- raticode-mobile-candidate-ready:"
# Pushes to this branch prepare candidates. Main receives them by squash merge.
CANDIDATE_BRANCH = "stage"
TAG_PREFIX = app_version.TAG_PREFIX
# This app's folder in the repository it shares with the desktop app.
PRODUCT_PATH = "mobile/android"
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
RELEASE_APK = ROOT / "app/build/outputs/apk/release/app-release.apk"
UNSIGNED_APK = ROOT / "app/build/outputs/apk/release/app-release-unsigned.apk"
# Gradle names the bundle the same whether it signed it or not.
RELEASE_BUNDLE = ROOT / "app/build/outputs/bundle/release/app-release.aab"
# Google Play's limit on a base module's compressed download (500 MB). Above 200 MB,
# Play warns people on mobile data before they install.
PLAY_DOWNLOAD_LIMIT = 500_000_000
PLAY_MOBILE_DATA_WARNING = 200_000_000
# Android 15+ devices can use 16 KB memory pages; Google Play requires 64-bit native
# libraries to support them.
PAGE_ALIGNMENT = 16 * 1024
MAPPING = ROOT / "app/build/outputs/mapping/release/mapping.txt"


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def gh(*args: str, output: Path | None = None) -> str:
    if output is not None:
        with output.open("wb") as stream:
            subprocess.run(["gh", *args], stdout=stream, check=True)
        return ""
    return subprocess.run(["gh", *args], check=True, text=True, stdout=subprocess.PIPE).stdout


def api(endpoint: str, payload: dict[str, Any] | None = None, method: str = "GET") -> Any:
    args = ["api", endpoint, "--method", method]
    if payload is None:
        return json.loads(gh(*args))
    with tempfile.TemporaryDirectory() as temporary:
        body = Path(temporary) / "body.json"
        body.write_text(json.dumps(payload), encoding="utf8")
        return json.loads(gh(*args, "--input", str(body)))


def pages(endpoint: str, key: str | None = None) -> list[dict[str, Any]]:
    result = []
    for page in range(1, 1001):
        response = api(f"{endpoint}?per_page=100&page={page}")
        items = response[key] if key else response
        result.extend(items)
        if len(items) < 100:
            return result
    raise ValueError("GitHub pagination exceeded 1000 pages")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def versions(root: Path = ROOT) -> tuple[str, int]:
    try:
        return app_version.read(root)
    except app_version.VersionError as error:
        raise ValueError(str(error)) from error


def policy() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(POLICY.read_text(encoding="utf8"))
    return result


def fingerprint(value: str | None) -> str:
    """A certificate SHA-256 in lowercase hex, from keytool's AB:CD:... form or apksigner's."""
    text = re.sub(r"[\s:]", "", (value or "").lower())
    require(re.fullmatch(r"[0-9a-f]{64}", text), "Expected a 64-digit SHA-256 certificate fingerprint")
    return text


def expected_certificate() -> str:
    value = os.environ.get("ANDROID_SIGNING_CERT_SHA256", "")
    require(
        value.strip(),
        "Set the repository variable ANDROID_SIGNING_CERT_SHA256 to the release certificate's "
        "SHA-256 fingerprint. Signed APKs are checked against it.",
    )
    return fingerprint(value)


def apk_name(version: str) -> str:
    return f"raticode-mobile-{version}.apk"


def bundle_name(version: str) -> str:
    return f"raticode-mobile-{version}.aab"


def required_files(version: str, signed: bool = False) -> dict[str, set[str]]:
    android = {apk_name(version), "apk-checks.json", "device-tests.json", "checksums-android.txt"}
    if signed:
        # The App Bundle for Google Play, signed with the same upload key. Unsigned
        # candidates never carry one: Play refuses an unsigned upload.
        android |= {bundle_name(version), "bundle-checks.json"}
    return {"android": android, "audit-evidence": {"dependency-inventory.json", "dependency-audit.json"}}


# Android tooling ----------------------------------------------------------------------


def sdk_tool(*parts: str) -> Path:
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    require(home, "Set ANDROID_HOME to the Android SDK")
    path = Path(str(home), *parts)
    require(path.is_file(), f"{path.name} not found in the Android SDK at {Path(*parts)}")
    return path


def build_tool(name: str) -> Path:
    return sdk_tool("build-tools", os.environ.get("RATICODE_ANDROID_BUILD_TOOLS", "36.0.0"), name)


def java_tool(name: str) -> Path:
    home = os.environ.get("JAVA_HOME")
    path = Path(home, "bin", name) if home else Path(shutil.which(name) or name)
    require(path.is_file(), f"{name} not found; set JAVA_HOME to JDK 21")
    return path


def run(*args: Any, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        [str(a) for a in args], check=True, text=True, stdout=subprocess.PIPE, env=env
    ).stdout


def badging(apk: Path) -> dict[str, Any]:
    text = run(build_tool("aapt2"), "dump", "badging", apk)
    package = re.search(r"^package: name='([^']*)' versionCode='(\d+)' versionName='([^']*)'", text, re.M)
    sdk = re.search(r"^(?:minSdkVersion|sdkVersion):'(\d+)'", text, re.M)
    target = re.search(r"^targetSdkVersion:'(\d+)'", text, re.M)
    require(package and sdk and target, "Could not read the APK's package, version and SDK levels")
    assert package and sdk and target
    return {
        "package": package.group(1),
        "version_code": int(package.group(2)),
        "version_name": package.group(3),
        "min_sdk": int(sdk.group(1)),
        "target_sdk": int(target.group(1)),
        "debuggable": bool(re.search(r"^application-debuggable", text, re.M)),
        "test_only": bool(re.search(r"^testOnly='-1'", text, re.M)),
        "permissions": sorted(set(re.findall(r"^uses-permission: name='([^']+)'", text, re.M))),
    }


ELEMENT = re.compile(r"^(\s*)E: (\S+) \(line=\d+\)")
ATTRIBUTE = re.compile(r"^(\s*)A: (?:\S+:)?([\w-]+)(?:\(0x[0-9a-f]+\))?=(.*)$")


def attribute_value(raw: str) -> Any:
    raw = raw.strip()
    quoted = re.match(r'^"(.*?)"(?: \(Raw: .*\))?$', raw)
    if quoted:
        return quoted.group(1)
    # A bundle's proto manifest prints booleans as `false (Raw: "false")`.
    raw = re.sub(r' \(Raw: ".*"\)$', "", raw)
    if raw in ("true", "(type 0x12)0xffffffff"):
        return True
    if raw in ("false", "(type 0x12)0x0"):
        return False
    return raw


def parse_manifest_tree(text: str) -> dict[str, Any]:
    """Turn `aapt2 dump xmltree` output into nested {tag, attrs, children} elements."""
    root: dict[str, Any] = {"tag": "", "attrs": {}, "children": [], "depth": -1}
    stack = [root]
    for line in text.splitlines():
        element = ELEMENT.match(line)
        attribute = ATTRIBUTE.match(line)
        if element:
            depth = len(element.group(1))
            while stack[-1]["depth"] >= depth:
                stack.pop()
            node = {"tag": element.group(2), "attrs": {}, "children": [], "depth": depth}
            stack[-1]["children"].append(node)
            stack.append(node)
        elif attribute and len(stack) > 1:
            stack[-1]["attrs"][attribute.group(2)] = attribute_value(attribute.group(3))
    manifests = [c for c in root["children"] if c["tag"] == "manifest"]
    require(len(manifests) == 1, "Could not read the APK's manifest")
    return manifests[0]


def exported_components(manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str | None]]:
    applications = [c for c in manifest["children"] if c["tag"] == "application"]
    require(len(applications) == 1, "The APK manifest must have one application element")
    application = applications[0]
    exported = {
        child["attrs"].get("name"): child["attrs"].get("permission")
        for child in application["children"]
        if child["tag"] in {"activity", "activity-alias", "service", "receiver", "provider"}
        and child["attrs"].get("exported") is True
    }
    return application["attrs"], exported


def signature(apk: Path) -> dict[str, Any] | None:
    """apksigner's verdict, or None when the APK is unsigned or its signature fails."""
    result = subprocess.run(
        [str(build_tool("apksigner")), "verify", "--verbose", "--print-certs", str(apk)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        return None
    text = result.stdout
    schemes = re.findall(r"^Verified using (v[\d.]+) scheme[^:]*: true$", text, re.M)
    return {
        "signers": int((re.search(r"^Number of signers: (\d+)$", text, re.M) or [0, 0])[1]),
        "schemes": schemes,
        "names": re.findall(r"^Signer #\d+ certificate DN: (.*)$", text, re.M),
        "certificates": re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]{64})$", text, re.M),
    }


def difference(label: str, actual: Any, expected: Any) -> str:
    added, removed = sorted(set(actual) - set(expected)), sorted(set(expected) - set(actual))
    changed = sorted(k for k in set(actual) & set(expected) if isinstance(actual, dict) and actual[k] != expected[k])
    return (
        f"{label} changed (added {added}, removed {removed}, changed {changed}). Review the change, "
        "then update scripts/release-policy.json in the same commit."
    )


def native_alignment(archive: Path, prefix: str = "lib/") -> dict[str, Any]:
    """Every 64-bit native library's smallest LOAD segment alignment, read from its ELF header."""
    problems, libraries = [], 0
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            if not (entry.filename.startswith(prefix) and entry.filename.endswith(".so")):
                continue
            with bundle.open(entry) as stream:
                header = stream.read(64)
                if len(header) < 64 or header[:4] != b"\x7fELF" or header[4] != 2:
                    continue  # 32-bit or not ELF; this app ships 64-bit libraries only
                endian = "<" if header[5] == 1 else ">"
                phoff, = struct.unpack_from(endian + "Q", header, 0x20)
                phentsize, phnum = struct.unpack_from(endian + "HH", header, 0x36)
                stream.read(phoff - 64)
                table = stream.read(phentsize * phnum)
            libraries += 1
            aligns = [
                struct.unpack_from(endian + "Q", table, i * phentsize + 0x30)[0]
                for i in range(phnum)
                if struct.unpack_from(endian + "I", table, i * phentsize)[0] == 1
            ]
            if not aligns or min(aligns) < PAGE_ALIGNMENT:
                problems.append(f"{entry.filename} ({min(aligns) if aligns else 'no LOAD'})")
    require(
        not problems,
        f"Native libraries are not aligned for 16 KB memory pages, which Google Play requires: {problems}",
    )
    return {"libraries": libraries, "page_alignment": PAGE_ALIGNMENT}


def manifest_policy(info: dict[str, Any], tree: dict[str, Any]) -> tuple[str, int, dict[str, str | None]]:
    """The checks an APK and an App Bundle share: identity, SDK levels, permissions and exports."""
    version, code = versions()
    rules = policy()
    require(info["package"] == rules["package"], f"Package is {info['package']}, not {rules['package']}")
    require(
        (info["version_name"], info["version_code"]) == (version, code),
        f"Build is {info['version_name']} ({info['version_code']}); version.properties says {version} ({code})",
    )
    require(
        (info["min_sdk"], info["target_sdk"]) == (rules["min_sdk"], rules["target_sdk"]),
        f"Build targets SDK {info['min_sdk']}-{info['target_sdk']}, policy says "
        f"{rules['min_sdk']}-{rules['target_sdk']}",
    )
    require(not info["debuggable"] and not info["test_only"], "Release build is debuggable or test-only")
    require(
        info["permissions"] == sorted(rules["permissions"]),
        difference("Permissions", info["permissions"], rules["permissions"]),
    )
    application, exported = exported_components(tree)
    require(application.get("allowBackup") is False, "Release build must set allowBackup=false")
    require(application.get("usesCleartextTraffic") is False, "Release build must set usesCleartextTraffic=false")
    require(application.get("debuggable") is not True, "Release build is debuggable")
    require(exported == rules["exported"], difference("Exported components", exported, rules["exported"]))
    return version, code, exported


def check_apk(apk: Path, signed: bool, mapping: Path | None = None) -> dict[str, Any]:
    """Check a release APK against version.properties and scripts/release-policy.json."""
    require(apk.is_file() and apk.stat().st_size > 0, f"No APK at {apk}")
    info = badging(apk)
    tree = parse_manifest_tree(run(build_tool("aapt2"), "dump", "xmltree", "--file", "AndroidManifest.xml", apk))
    version, code, exported = manifest_policy(info, tree)
    alignment = native_alignment(apk)
    verdict = signature(apk)
    certificate = None
    if signed:
        require(verdict, "The APK's signature does not verify")
        assert verdict is not None
        require(verdict["signers"] == 1 and len(verdict["certificates"]) == 1, "Expected exactly one signer")
        require({"v2", "v3"} & set(verdict["schemes"]), "Expected an APK Signature Scheme v2 or v3 signature")
        require(
            not any("CN=Android Debug" in name for name in verdict["names"]),
            "The APK is signed with Android's debug key",
        )
        certificate = verdict["certificates"][0]
        require(
            certificate == expected_certificate(),
            "The APK is signed with a different certificate than ANDROID_SIGNING_CERT_SHA256. "
            "Phones with an earlier release would refuse it as an update.",
        )
    else:
        require(verdict is None, "Expected an unsigned APK, but its signature verifies")
    if mapping is not None:
        require(mapping.is_file() and mapping.stat().st_size > 0, "R8 mapping file is missing")
    return {
        "status": "passed",
        "package": info["package"],
        "version": version,
        "version_code": code,
        "min_sdk": info["min_sdk"],
        "target_sdk": info["target_sdk"],
        "signed": signed,
        "certificate_sha256": certificate,
        "signature_schemes": verdict["schemes"] if verdict else [],
        "apk_sha256": digest(apk),
        "apk_size": apk.stat().st_size,
        "mapping_sha256": digest(mapping) if mapping else None,
        "permissions": info["permissions"],
        "exported": exported,
        "native_alignment": alignment,
    }


def jar_signature(archive: Path) -> dict[str, Any] | None:
    """jarsigner's verdict on an App Bundle, or None when it is unsigned or fails to verify.

    Bundles carry a JAR (v1) signature; Play checks it against the registered upload key.
    """
    result = subprocess.run(
        [str(java_tool("jarsigner")), "-verify", str(archive)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if result.returncode != 0 or "jar verified." not in result.stdout:
        return None
    text = run(java_tool("keytool"), "-printcert", "-jarfile", archive)
    owners = re.findall(r"^Owner: (.*)$", text, re.M)
    certificates = [fingerprint(v) for v in re.findall(r"^\s*SHA256: ([0-9A-F:]{95})$", text, re.M)]
    return {"signers": len(re.findall(r"^Signer #\d+:", text, re.M)), "names": owners, "certificates": certificates}


def bundle_base(aab: Path, directory: Path) -> Path:
    """The bundle's base module as a proto-format APK, which aapt2 can read like any APK."""
    base = directory / "base-module.apk"
    with zipfile.ZipFile(aab) as bundle, zipfile.ZipFile(base, "w") as output:
        names = bundle.namelist()
        require("base/manifest/AndroidManifest.xml" in names, "The bundle has no base module manifest")
        for name in names:
            if not name.startswith("base/") or name.endswith("/"):
                continue
            inner = name.removeprefix("base/")
            inner = "AndroidManifest.xml" if inner == "manifest/AndroidManifest.xml" else inner
            if inner.startswith(("res/", "resources.pb", "AndroidManifest.xml")):
                output.writestr(inner, bundle.read(name))
    return base


def bundle_download_estimate(aab: Path) -> dict[str, Any]:
    """Upper bound on what one phone downloads: every non-native file plus its largest ABI.

    Play serves a phone only its own ABI's libraries, language and density, so the real
    download is smaller. Native libraries are counted at their compressed size here.
    """
    shared, per_abi = 0, {}
    with zipfile.ZipFile(aab) as bundle:
        for entry in bundle.infolist():
            parts = entry.filename.split("/")
            if parts[0] == "BUNDLE-METADATA" or parts[0] == "META-INF":
                continue
            if len(parts) > 3 and parts[1] == "lib":
                per_abi[parts[2]] = per_abi.get(parts[2], 0) + entry.compress_size
            else:
                shared += entry.compress_size
    estimate = shared + max(per_abi.values(), default=0)
    require(
        estimate <= PLAY_DOWNLOAD_LIMIT,
        f"The bundle could give a phone {estimate} bytes to download; Google Play allows {PLAY_DOWNLOAD_LIMIT}",
    )
    return {
        "abis": sorted(per_abi),
        "largest_download_estimate": estimate,
        "limit": PLAY_DOWNLOAD_LIMIT,
        "mobile_data_warning": estimate > PLAY_MOBILE_DATA_WARNING,
    }


def check_bundle(aab: Path, signed: bool, apk_certificate: str | None = None) -> dict[str, Any]:
    """Check the release App Bundle with the APK's policy, plus Play's size and signing rules."""
    require(aab.is_file() and aab.stat().st_size > 0, f"No App Bundle at {aab}")
    with tempfile.TemporaryDirectory() as temporary:
        base = bundle_base(aab, Path(temporary))
        info = badging(base)
        tree = parse_manifest_tree(
            run(build_tool("aapt2"), "dump", "xmltree", "--file", "AndroidManifest.xml", base)
        )
    version, code, exported = manifest_policy(info, tree)
    alignment = native_alignment(aab, prefix="base/lib/")
    size = bundle_download_estimate(aab)
    verdict = jar_signature(aab)
    certificate = None
    if signed:
        require(verdict, "The App Bundle's signature does not verify")
        assert verdict is not None
        require(verdict["signers"] == 1 and len(verdict["certificates"]) == 1, "Expected exactly one bundle signer")
        require(
            not any("CN=Android Debug" in name for name in verdict["names"]),
            "The App Bundle is signed with Android's debug key",
        )
        certificate = verdict["certificates"][0]
        require(
            certificate == expected_certificate(),
            "The App Bundle is signed with a different certificate than ANDROID_SIGNING_CERT_SHA256",
        )
        require(
            apk_certificate is None or certificate == apk_certificate,
            "The App Bundle and the APK are signed with different keys",
        )
    else:
        require(verdict is None, "Expected an unsigned App Bundle, but its signature verifies")
    return {
        "status": "passed",
        "package": info["package"],
        "version": version,
        "version_code": code,
        "min_sdk": info["min_sdk"],
        "target_sdk": info["target_sdk"],
        "signed": signed,
        "certificate_sha256": certificate,
        "aab_sha256": digest(aab),
        "aab_size": aab.stat().st_size,
        "permissions": info["permissions"],
        "exported": exported,
        "native_alignment": alignment,
        "download": size,
    }


def collect(output: Path, signed: bool) -> Path:
    """Check the built release APK and copy it into an empty candidate folder."""
    apk = RELEASE_APK if signed else UNSIGNED_APK
    folder = output / "android"
    folder.mkdir(parents=True, exist_ok=True)
    require(not list(folder.iterdir()), f"{folder} must be empty")
    evidence = check_apk(apk, signed, MAPPING)
    # The bundle is checked either way, so an unsigned run still proves it would pass.
    bundle = check_bundle(RELEASE_BUNDLE, signed, evidence["certificate_sha256"])
    target = folder / apk_name(evidence["version"])
    shutil.copyfile(apk, target)
    require(digest(target) == evidence["apk_sha256"], "Copied APK differs from the checked one")
    (folder / "apk-checks.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    if signed:
        aab = folder / bundle_name(bundle["version"])
        shutil.copyfile(RELEASE_BUNDLE, aab)
        require(digest(aab) == bundle["aab_sha256"], "Copied App Bundle differs from the checked one")
        (folder / "bundle-checks.json").write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    return target


def osv_query(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        OSV_BATCH,
        data=json.dumps({"queries": queries}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - fixed https URL
        results: list[dict[str, Any]] = json.load(response)["results"]
    return results


def audit(inventory_path: Path, output: Path) -> dict[str, Any]:
    """Look up every shipped dependency in OSV. Unreviewed advisories fail the candidate."""
    inventory = json.loads(inventory_path.read_text(encoding="utf8"))
    packages = inventory.get("releaseRuntime") or []
    require(packages, "The dependency inventory lists no release runtime dependencies")
    accepted = policy().get("accepted_advisories", {})
    results: list[dict[str, Any]] = []
    for start in range(0, len(packages), 500):
        batch = packages[start:start + 500]
        answer = osv_query([
            {"package": {"ecosystem": "Maven", "name": f"{p['group']}:{p['name']}"}, "version": p["version"]}
            for p in batch
        ])
        require(len(answer) == len(batch), "OSV returned an incomplete answer")
        results.extend(answer)
    findings = []
    for package, result in zip(packages, results, strict=True):
        ids = sorted({v["id"] for v in result.get("vulns", [])})
        if ids:
            findings.append({
                "package": f"{package['group']}:{package['name']}",
                "version": package["version"],
                "advisories": ids,
                "unreviewed": [i for i in ids if i not in accepted],
            })
    blocking = [f for f in findings if f["unreviewed"]]
    report = {
        "status": "failed" if blocking else "passed",
        "source": OSV_BATCH,
        "scope": "releaseRuntimeClasspath",
        "checked": len(packages),
        "checked_at": datetime.now(UTC).isoformat(),
        "findings": findings,
    }
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(inventory_path, output / "dependency-inventory.json")
    (output / "dependency-audit.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    require(
        not blocking,
        "Unreviewed advisories: "
        + ", ".join(f"{f['package']} {f['version']} ({', '.join(f['unreviewed'])})" for f in blocking)
        + ". Update the dependency, or record a reviewed reason in accepted_advisories.",
    )
    return report


def instrumented_results(directory: Path, strict: bool = True) -> dict[str, int]:
    """Totals of the instrumented test reports.

    With ``strict``, any failure or error is refused. Without it, failures are only
    counted: the release candidate records emulator results without blocking on them.
    No reports, or reports that ran no tests, are refused either way, because that
    means the tests never ran (for example the test APK didn't build).
    """
    reports = sorted(directory.rglob("TEST-*.xml"))
    require(reports, f"No instrumented test results under {directory}")
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for path in reports:
        suite = ET.parse(path).getroot()
        for key in totals:
            totals[key] += int(suite.get(key, "0"))
    require(totals["tests"] > 0, "Instrumented tests ran no tests")
    if strict:
        require(totals["failures"] == totals["errors"] == 0, f"Instrumented tests failed: {totals}")
    return totals


def instrumented_summary(results: dict[str, Any]) -> dict[str, Any]:
    """Recorded emulator results, with whether they passed. They never block a candidate."""
    totals = {key: int(results.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}
    exit_code = int(results.get("exit_code", 0))
    passed = totals["tests"] > 0 and totals["failures"] == totals["errors"] == 0 and exit_code == 0
    return {**totals, "exit_code": exit_code, "passed": passed}


def failed_tests(directory: Path, limit: int = 20) -> list[str]:
    """Names of failed or errored instrumented tests, for the job summary."""
    names: list[str] = []
    for path in sorted(directory.rglob("TEST-*.xml")):
        for case in ET.parse(path).getroot().iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                names.append(f"{case.get('classname', '')}.{case.get('name', '')}".strip("."))
    return names[:limit]


def ephemeral_sign(apk: Path, directory: Path) -> Path:
    """Sign a copy with a throwaway key so an unsigned candidate can be launched."""
    keytool = Path(os.environ["JAVA_HOME"], "bin/keytool") if os.environ.get("JAVA_HOME") else None
    keytool_path = str(keytool) if keytool and keytool.is_file() else shutil.which("keytool")
    require(keytool_path, "keytool not found; set JAVA_HOME")
    store = directory / "launch-test.p12"
    env = {**os.environ, "RATICODE_LAUNCH_TEST_PASS": secrets.token_hex(16)}
    run(keytool_path, "-genkeypair", "-keystore", store, "-storetype", "PKCS12", "-alias", "launch-test",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "1", "-dname", "CN=Raticode launch test",
        "-storepass:env", "RATICODE_LAUNCH_TEST_PASS", "-keypass:env", "RATICODE_LAUNCH_TEST_PASS", env=env)
    signed = directory / "launch-test.apk"
    run(build_tool("apksigner"), "sign", "--ks", store, "--ks-pass", "env:RATICODE_LAUNCH_TEST_PASS",
        "--ks-key-alias", "launch-test", "--out", signed, apk, env=env)
    return signed


def launch(apk: Path, serial: str | None = None, settle: float = 5.0) -> dict[str, Any]:
    """Install the release APK on an emulator, start it and confirm it keeps running."""
    adb = sdk_tool("platform-tools", "adb")
    if not serial:
        serial = os.environ.get("ANDROID_SERIAL")
    if not serial:
        devices = re.findall(r"^(\S+)\tdevice$", run(adb, "devices"), re.M)
        require(len(devices) == 1, "Connect exactly one emulator or set ANDROID_SERIAL")
        serial = devices[0]

    def device(*args: str) -> str:
        return run(adb, "-s", serial, *args)

    # Installing replaces the app and its data, so never touch a real phone.
    require(
        serial.startswith("emulator-")
        and "1" in {device("shell", "getprop", p).strip() for p in ("ro.kernel.qemu", "ro.boot.qemu")},
        f"{serial} is not an emulator. Launch tests reinstall the app; run them on an emulator only.",
    )
    package = policy()["package"]
    version, code = versions()
    with tempfile.TemporaryDirectory() as temporary:
        signer = "release key"
        install = apk
        if signature(apk) is None:
            install, signer = ephemeral_sign(apk, Path(temporary)), "throwaway test key"
        subprocess.run([str(adb), "-s", serial, "uninstall", package], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        try:
            device("install", "-r", str(install))
            device("logcat", "-b", "crash", "-c")
            started = device("shell", "am", "start", "-W", "-n", f"{package}/.MainActivity")
            require("Status: ok" in started, "MainActivity did not start")
            time.sleep(settle)
            require(device("shell", "pidof", package).strip(), "The app stopped soon after launch")
            # Count crash records only; the log itself is never copied into evidence.
            require(f"Process: {package}" not in device("logcat", "-b", "crash", "-d"), "The app crashed after launch")
            dump = device("shell", "dumpsys", "package", package)
            require(
                f"versionName={version}\n" in dump.replace("\r", "") and f"versionCode={code} " in dump,
                "The installed app reports a different version",
            )
            flags = re.search(r"pkgFlags=\[([^\]]*)\]", dump)
            require(flags and "DEBUGGABLE" not in flags.group(1), "The installed app is debuggable")
            api_level = int(device("shell", "getprop", "ro.build.version.sdk").strip())
        finally:
            subprocess.run([str(adb), "-s", serial, "uninstall", package], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
    return {"launched": True, "api_level": api_level, "signer": signer}


def device_test(
    folder: Path, results: Path | None, serial: str | None = None, instrumented_exit: int = 0
) -> dict[str, Any]:
    """Record the emulator test results and launch the release APK.

    The launch of the release APK must succeed. Failed instrumented tests are recorded
    with their counts and Gradle's exit code but don't stop the candidate: they are
    reported as non-blocking in the job summary and the release notes.
    """
    version, code = versions()
    apk = folder / apk_name(version)
    instrumented = None
    if results:
        instrumented = instrumented_summary(
            {**instrumented_results(results, strict=False), "exit_code": instrumented_exit}
        )
        instrumented["failed"] = failed_tests(results)
    evidence = {
        "status": "passed",
        "version": version,
        "version_code": code,
        "apk_sha256": digest(apk),
        "instrumented": instrumented,
        "launch": launch(apk, serial),
    }
    (folder / "device-tests.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return evidence


def report_instrumented(folder: Path) -> dict[str, Any]:
    """Write the emulator results to the job summary and step outputs. Never fails on them."""
    tests = json.loads((folder / "device-tests.json").read_text())
    results = tests.get("instrumented") or {}
    summary = instrumented_summary(results)
    if summary["passed"]:
        text = (
            f"### Emulator tests passed\n\n{summary['tests']} instrumented tests ran "
            f"({summary['skipped']} skipped) and the release APK launched.\n"
        )
    else:
        failed = results.get("failed") or []
        listed = "".join(f"- `{name}`\n" for name in failed)
        text = (
            "### Emulator tests failed (non-blocking)\n\n"
            f"{summary['failures']} failures and {summary['errors']} errors in "
            f"{summary['tests']} instrumented tests (Gradle exit code {summary['exit_code']}). "
            "They don't block this candidate: the APK was still built, checked against the "
            "release policy and launched on the emulator, and the results are recorded in "
            "device-tests.json, the release notes and the candidate-reports artifact.\n\n"
            + (f"Failed tests:\n\n{listed}" if listed else "")
        )
        print(
            f"::warning title=Emulator tests failed (non-blocking)::{summary['failures']} failures and "
            f"{summary['errors']} errors in {summary['tests']} instrumented tests"
        )
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write(text)
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"instrumented_passed={str(summary['passed']).lower()}\n")
            stream.write(
                f"instrumented_result={summary['tests']} tests, {summary['failures']} failures, "
                f"{summary['errors']} errors\n"
            )
    return summary


def seal(folder: Path) -> None:
    name = f"checksums-{folder.name.removeprefix(ARTIFACT_PREFIX)}.txt"
    files = sorted(p for p in folder.iterdir() if p.name != name)
    require(files and all(p.is_file() and not p.is_symlink() for p in files), "Nothing to checksum")
    (folder / name).write_text("".join(f"{digest(p)}  {p.name}\n" for p in files))


# Candidate manifest, staging and publication -------------------------------------------


def verify_group(folder: Path, group: str, version: str, code: int, signed: bool = False) -> None:
    expected = required_files(version, signed)[group]
    actual = {p.name for p in folder.iterdir()}
    require(actual == expected, f"{group}: missing {sorted(expected - actual)}, unexpected {sorted(actual - expected)}")
    require(
        all(p.is_file() and not p.is_symlink() for p in folder.iterdir()),
        f"{group}: only regular files are accepted",
    )
    if group == "audit-evidence":
        report = json.loads((folder / "dependency-audit.json").read_text())
        require(
            report.get("status") == "passed" and report.get("checked", 0) > 0
            and not any(f.get("unreviewed") for f in report.get("findings", [])),
            "audit-evidence: dependency audit did not pass",
        )
        return
    checksums = {}
    for line in (folder / f"checksums-{group}.txt").read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        require(match, f"Malformed {group} checksum line")
        assert match is not None
        checksum, name = match.groups()
        require(name not in checksums, f"Duplicate checksum: {name}")
        checksums[name] = checksum
    require(
        set(checksums) == actual - {f"checksums-{group}.txt"},
        f"{group}: checksums do not cover exactly the uploaded files",
    )
    for name, checksum in checksums.items():
        require(digest(folder / name) == checksum, f"Checksum mismatch: {name}")
    apk_hash = digest(folder / apk_name(version))
    checks = json.loads((folder / "apk-checks.json").read_text())
    require(
        checks.get("status") == "passed"
        and (checks.get("version"), checks.get("version_code")) == (version, code)
        and checks.get("signed") is signed
        and checks.get("apk_sha256") == apk_hash,
        f"{group}: APK checks do not match this APK, version and signing policy",
    )
    if signed:
        fingerprint(checks.get("certificate_sha256"))
        bundle = json.loads((folder / "bundle-checks.json").read_text())
        require(
            bundle.get("status") == "passed"
            and (bundle.get("version"), bundle.get("version_code")) == (version, code)
            and bundle.get("signed") is True
            and bundle.get("certificate_sha256") == checks.get("certificate_sha256")
            and bundle.get("aab_sha256") == digest(folder / bundle_name(version)),
            f"{group}: App Bundle checks do not match this bundle, version and signing key",
        )
    tests = json.loads((folder / "device-tests.json").read_text())
    instrumented = tests.get("instrumented") or {}
    # The launch of this exact APK must have succeeded and the instrumented tests must
    # have run. Their failures are recorded, not refused: emulator tests don't block.
    require(
        tests.get("status") == "passed"
        and (tests.get("version"), tests.get("version_code")) == (version, code)
        and tests.get("apk_sha256") == apk_hash
        and (tests.get("launch") or {}).get("launched") is True
        and isinstance(instrumented.get("tests"), int)
        and instrumented["tests"] > 0
        and all(isinstance(instrumented.get(k), int) for k in ("failures", "errors")),
        f"{group}: missing instrumented results or a successful launch test for this APK",
    )


def create_manifest(
    source: Path,
    output: Path,
    repo: str,
    sha: str,
    run_id: int,
    attempt: int,
    artifacts: list[dict[str, Any]],
    signed: bool = False,
) -> dict[str, Any]:
    version, code = versions()
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Expected full source SHA")
    names = {f"{ARTIFACT_PREFIX}{g}" for g in GROUPS}
    require({p.name for p in source.iterdir()} == names, "Expected exactly the Android and audit artifacts")
    records = [a for a in artifacts if a["name"].startswith(ARTIFACT_PREFIX)]
    require(
        len(records) == len(GROUPS) and {a["name"] for a in records} == names,
        "Run artifact inventory is incomplete or ambiguous",
    )
    require(all(not a["expired"] and a["id"] for a in records), "Candidate artifacts expired")
    output.mkdir(exist_ok=True)
    require(not list(output.iterdir()), "Manifest output directory must be empty")
    files = {}
    for group in GROUPS:
        folder = source / f"{ARTIFACT_PREFIX}{group}"
        verify_group(folder, group, version, code, signed)
        for path in sorted(folder.iterdir()):
            require(path.name not in files, f"Artifact filename collision: {path.name}")
            files[path.name] = {"sha256": digest(path), "size": path.stat().st_size, "group": group}
            shutil.copyfile(path, output / path.name)
    checks = json.loads((source / f"{ARTIFACT_PREFIX}android/apk-checks.json").read_text())
    device = json.loads((source / f"{ARTIFACT_PREFIX}android/device-tests.json").read_text())
    instrumented = instrumented_summary(device["instrumented"])
    manifest = {
        "schema": 1,
        "repository": repo,
        "commit": sha,
        "version": version,
        "version_code": code,
        "prerelease": "-" in version,
        "workflow": WORKFLOW,
        "run_id": run_id,
        "run_attempt": attempt,
        "created_at": datetime.now(UTC).isoformat(),
        "signed": signed,
        "certificate_sha256": checks["certificate_sha256"] if signed else None,
        "artifacts": [{k: a[k] for k in ("id", "name", "digest")} for a in records],
        "checks": [
            "source-validation",
            "release-apk-policy",
            *(["instrumented-tests"] if instrumented["passed"] else []),
            "release-launch",
            "dependency-audit",
        ]
        + (["release-signature", "release-bundle-policy"] if signed else []),
        # Emulator results are recorded as they were; they never block a candidate.
        "instrumented_tests": instrumented,
        "files": files,
    }
    (output / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def validate_manifest(manifest: dict[str, Any], repo: str, sha: str, version: str, code: int) -> None:
    require(
        manifest.get("schema") == 1 and type(manifest.get("signed")) is bool,
        "Candidate must declare a boolean signing policy in schema 1",
    )
    for key, expected_value in (
        ("repository", repo),
        ("commit", sha),
        ("version", version),
        ("version_code", code),
        ("prerelease", "-" in version),
        ("workflow", WORKFLOW),
    ):
        require(manifest.get(key) == expected_value, f"Candidate {key} mismatch")
    require(
        ("release-signature" in manifest.get("checks", [])) == manifest["signed"],
        "Candidate signing checks disagree with its signing policy",
    )
    if manifest["signed"]:
        require(manifest.get("certificate_sha256") == fingerprint(manifest.get("certificate_sha256")),
                "Signed candidate has no certificate fingerprint")
    else:
        require(manifest.get("certificate_sha256") is None, "Unsigned candidate names a certificate")
    files = manifest["files"]
    require(
        ("release-bundle-policy" in manifest.get("checks", [])) == manifest["signed"],
        "Signed candidates, and only they, carry a checked App Bundle",
    )
    for group, expected in required_files(version, manifest["signed"]).items():
        actual = {name for name, info in files.items() if info["group"] == group}
        require(actual == expected, f"Candidate {group} inventory mismatch")
    for name, info in files.items():
        require(
            name not in {".", "..", MANIFEST} and re.fullmatch(r"[A-Za-z0-9_.-]+", name),
            "Unsafe candidate filename",
        )
        require(
            info["group"] in GROUPS and re.fullmatch(r"[0-9a-f]{64}", info["sha256"]),
            "Malformed candidate file metadata",
        )
        require(isinstance(info["size"], int) and info["size"] > 0, "Empty candidate asset")
    require(
        isinstance(manifest["run_id"], int)
        and manifest["run_id"] > 0
        and isinstance(manifest["run_attempt"], int)
        and manifest["run_attempt"] > 0,
        "Invalid source run identity",
    )


def validate_run(run_record: dict[str, Any], sha: str, repo: str) -> None:
    require(
        run_record.get("conclusion") == "success" and run_record.get("status") == "completed",
        "Candidate preparation run has not succeeded. Wait, then rerun publication.",
    )
    require(
        run_record.get("head_sha") == sha
        and run_record.get("head_branch") == CANDIDATE_BRANCH
        and run_record.get("event") in {"push", "workflow_dispatch"}
        and run_record.get("path") == WORKFLOW
        and run_record.get("repository", {}).get("full_name", "").lower() == repo.lower(),
        "Candidate must come from this repository's trusted stage preparation workflow",
    )


def assets(repo: str, release_id: int) -> list[dict[str, Any]]:
    return pages(f"repos/{repo}/releases/{release_id}/assets")


def download(repo: str, asset: dict[str, Any], destination: Path) -> None:
    gh(
        "api",
        f"repos/{repo}/releases/assets/{asset['id']}",
        "-H",
        "Accept: application/octet-stream",
        output=destination,
    )


def verify_remote(
    repo: str,
    release: dict[str, Any],
    directory: Path,
    manifest: dict[str, Any],
    manifest_hash: str,
) -> None:
    entries = assets(repo, release["id"])
    expected = {**manifest["files"], MANIFEST: {"sha256": manifest_hash}}
    require(
        len(entries) == len(expected) and {a["name"] for a in entries} == set(expected),
        "Remote release has missing, duplicate or unexpected assets",
    )
    for entry in entries:
        target = directory / entry["name"]
        download(repo, entry, target)
        info = expected[entry["name"]]
        require(digest(target) == info["sha256"], f"Remote hash mismatch: {entry['name']}")
        if "size" in info:
            require(target.stat().st_size == info["size"], f"Remote size mismatch: {entry['name']}")


def check_upgrade(repo: str, manifest: dict[str, Any], directory: Path, skip: int | None = None) -> None:
    """Android installs an update only over a lower version code signed with the same key."""
    for release in pages(f"repos/{repo}/releases"):
        if release["draft"] or release["id"] == skip or not release["tag_name"].startswith(TAG_PREFIX):
            continue
        entries = [a for a in assets(repo, release["id"]) if a["name"] == MANIFEST]
        if len(entries) != 1:
            continue
        path = directory / f"published-{release['id']}.json"
        download(repo, entries[0], path)
        previous = json.loads(path.read_text())
        require(
            manifest["version_code"] > previous.get("version_code", 0),
            f"Version code {manifest['version_code']} is not higher than {release['tag_name']}'s "
            f"{previous.get('version_code')}. Phones with that release would refuse this update.",
        )
        if manifest["signed"] and previous.get("signed"):
            require(
                manifest["certificate_sha256"] == previous.get("certificate_sha256"),
                f"This candidate is signed with a different key than {release['tag_name']}. "
                "Android refuses to install it over that release.",
            )


def product_tree(repo: str, sha: str) -> str:
    """The Git tree of this app's folder at a commit."""
    tree = api(f"repos/{repo}/git/commits/{sha}")["tree"]["sha"]
    for name in PRODUCT_PATH.split("/"):
        entries = api(f"repos/{repo}/git/trees/{tree}")["tree"]
        matches = [e["sha"] for e in entries if e["path"] == name and e["type"] == "tree"]
        require(len(matches) == 1, f"{PRODUCT_PATH} is missing from commit {sha}")
        tree = matches[0]
    return tree


def same_source(repo: str, candidate: str, released: str) -> bool:
    """Whether the released commit has exactly the candidate's app files.

    A squash merge of stage into main makes a new commit; this app's folder is
    identical to the staged commit's when main held nothing stage didn't already
    contain. Changes elsewhere in the repository belong to the desktop app.
    """
    if candidate == released:
        return True
    return product_tree(repo, candidate) == product_tree(repo, released)


def previous_tag(repo: str, tag: str) -> str | None:
    """This app's most recently published tag, where its release notes should start."""
    published = [
        r
        for r in pages(f"repos/{repo}/releases")
        if not r["draft"] and r["tag_name"].startswith(TAG_PREFIX) and r["tag_name"] != tag
    ]
    if not published:
        return None
    return max(published, key=lambda r: r.get("published_at") or "")["tag_name"]


def candidate_tag(manifest: dict[str, Any]) -> str:
    return f"mobile-candidate-{manifest['commit']}-{manifest['run_id']}-{manifest['run_attempt']}"


def stage(repo: str, directory: Path) -> None:
    manifest_path = directory / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    validate_manifest(manifest, repo, os.environ["GITHUB_SHA"], *versions())
    verify_provenance(repo, manifest_path, manifest["commit"])
    with tempfile.TemporaryDirectory() as temporary:
        check_upgrade(repo, manifest, Path(temporary))
    tag = candidate_tag(manifest)
    matches = [r for r in pages(f"repos/{repo}/releases") if r["tag_name"] == tag]
    require(len(matches) <= 1, "Ambiguous candidate draft")
    release = (
        matches[0]
        if matches
        else api(
            f"repos/{repo}/releases",
            {
                "tag_name": tag,
                "target_commitish": manifest["commit"],
                "draft": True,
                "name": f"Candidate {TAG_PREFIX}{manifest['version']} / {manifest['commit'][:12]}",
                "body": "Candidate upload in progress. Do not publish manually.",
            },
            "POST",
        )
    )
    require(release["draft"], "Refusing to change a public release")
    # Resume only identical assets. Never use --clobber or mutate an uploaded file.
    existing = {a["name"]: a for a in assets(repo, release["id"])}
    expected = {**manifest["files"], MANIFEST: {"sha256": digest(manifest_path)}}
    require(set(existing) <= set(expected), "Draft contains unexpected files")
    with tempfile.TemporaryDirectory() as temporary:
        remote = Path(temporary)
        for name, info in expected.items():
            require(digest(directory / name) == info["sha256"], f"Local hash mismatch: {name}")
            if name in existing:
                download(repo, existing[name], remote / name)
                require(
                    digest(remote / name) == info["sha256"], f"Existing draft asset differs: {name}"
                )
            else:
                gh("release", "upload", tag, str(directory / name), "--repo", repo)
        verify_remote(repo, release, remote, manifest, digest(manifest_path))
    release_tag = f"{TAG_PREFIX}{manifest['version']}"
    previous = previous_tag(repo, release_tag)
    notes = api(
        f"repos/{repo}/releases/generate-notes",
        {
            "tag_name": release_tag,
            "target_commitish": manifest["commit"],
            **({"previous_tag_name": previous} if previous else {}),
        },
        "POST",
    )
    if manifest["signed"]:
        signing_note = (
            "Signed with the release key whose SHA-256 certificate fingerprint is "
            f"`{manifest['certificate_sha256']}`. Check it with `apksigner verify --print-certs` "
            "before installing. Android installs this over an earlier release only when both "
            "fingerprints match; a debug build must be uninstalled first, which deletes its pairing."
        )
    else:
        signing_note = (
            "Unsigned build evidence. Android cannot install an unsigned APK, "
            "and the publish job refuses unsigned candidates."
        )
    tests_note = emulator_note(manifest.get("instrumented_tests"))
    body = (
        notes["body"] + "\n\n" + signing_note + "\n\n" + tests_note + "\n\n"
        f"Version code: {manifest['version_code']}\n\n"
        f"Source: `{manifest['commit']}`\n\n"
        f"Preparation: https://github.com/{repo}/actions/runs/{manifest['run_id']}"
        f"/attempts/{manifest['run_attempt']}\n\n"
        f"{READY}{digest(manifest_path)} -->"
    )
    api(f"repos/{repo}/releases/{release['id']}", {"body": body}, "PATCH")
    summary = (
        f"Candidate staged: {release['html_url']}\n\n"
        "After this run succeeds, review the draft files and install the APK on a test phone. "
        f"Then push the {TAG_PREFIX}{manifest['version']} tag at the source commit shown below, "
        "or at its squash merge into main if that commit has exactly the same files. "
        "Do not use the web Publish button.\n\n" + body + "\n"
    )
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write(summary)


def emulator_note(results: dict[str, Any] | None) -> str:
    """The release notes' line about the emulator tests, as they actually ran."""
    if not results:
        return "Emulator tests: no results recorded."
    summary = instrumented_summary(results)
    if summary["passed"]:
        return f"Emulator tests: {summary['tests']} instrumented tests passed ({summary['skipped']} skipped)."
    return (
        f"Emulator tests: {summary['failures']} failures and {summary['errors']} errors in "
        f"{summary['tests']} instrumented tests. They were non-blocking for this candidate; "
        "see device-tests.json and the preparation run's candidate-reports artifact."
    )


def verify_provenance(repo: str, path: Path, sha: str) -> None:
    gh(
        "attestation",
        "verify",
        str(path),
        "--repo",
        repo,
        "--signer-workflow",
        f"{repo}/{WORKFLOW}",
        "--source-digest",
        sha,
        "--source-ref",
        f"refs/heads/{CANDIDATE_BRANCH}",
    )


def read_candidate(
    repo: str, release: dict[str, Any], directory: Path, sha: str, version: str, code: int
) -> dict[str, Any]:
    entries = [a for a in assets(repo, release["id"]) if a["name"] == MANIFEST]
    require(len(entries) == 1, "Draft has no unique manifest")
    path = directory / MANIFEST
    download(repo, entries[0], path)
    require(
        f"{READY}{digest(path)} -->" in (release.get("body") or ""),
        "Candidate remote verification has not completed",
    )
    manifest: dict[str, Any] = json.loads(path.read_text())
    source = manifest.get("commit")
    require(isinstance(source, str) and re.fullmatch(r"[0-9a-f]{40}", source), "Bad source SHA")
    require(
        same_source(repo, source, sha),
        "The tagged commit's files differ from the candidate's staged commit",
    )
    # The attestation binds the manifest to the staged commit it was built from.
    verify_provenance(repo, path, source)
    validate_manifest(manifest, repo, source, version, code)
    require(
        manifest["signed"],
        "Unsigned candidates cannot be published; Android won't install an unsigned APK. "
        "Set up release signing (docs/releasing.md) and prepare a signed candidate.",
    )
    require(
        manifest["certificate_sha256"] == expected_certificate(),
        "Candidate certificate differs from ANDROID_SIGNING_CERT_SHA256",
    )
    run_record = api(f"repos/{repo}/actions/runs/{manifest['run_id']}/attempts/{manifest['run_attempt']}")
    validate_run(run_record, source, repo)
    return manifest


def publish(repo: str) -> None:
    tag = os.environ["GITHUB_REF_NAME"]
    version, code = versions()
    require(tag == f"{TAG_PREFIX}{version}", f"Tag {tag} does not match version {TAG_PREFIX}{version}")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], text=True, stdout=subprocess.PIPE, check=True
    ).stdout.strip()
    # Peel annotated tags on the server too; refuse a tag moved after this run began.
    require(api(f"repos/{repo}/commits/{tag}")["sha"] == sha, "Remote tag moved")
    releases = pages(f"repos/{repo}/releases")
    public = [r for r in releases if r["tag_name"] == tag]
    require(len(public) <= 1, "Ambiguous version release")
    # Draft tag names are editable metadata. Discover ready drafts by their marker;
    # read_candidate verifies them through the attested manifest and source run.
    candidates = public or [r for r in releases if r["draft"] and READY in (r.get("body") or "")]
    require(
        candidates,
        "No verified draft for this commit. Finish Prepare mobile release candidate on stage, "
        "then rerun this publish job. The tag job never builds missing candidates.",
    )
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        ready = []
        for release in candidates:
            try:
                manifest = read_candidate(repo, release, directory, sha, version, code)
            except (ValueError, subprocess.CalledProcessError) as error:
                if public:
                    raise
                print(f"Skipping unusable draft {release['id']}: {error}")
                continue
            ready.append((release, manifest))
        # A candidate built from the tagged commit itself wins over ones that only
        # share its files, such as an earlier stage push of the same content.
        ready = [item for item in ready if item[1]["commit"] == sha] or ready
        require(
            len(ready) == 1,
            "Expected exactly one verified, successful, signed candidate for this commit. "
            "Wait for preparation or remove superseded drafts, then rerun publication.",
        )
        release, manifest = ready[0]
        if release["draft"]:
            age = datetime.now(UTC) - datetime.fromisoformat(manifest["created_at"])
            require(
                timedelta(0) <= age <= MAX_AGE,
                "Candidate is older than seven days. Prepare and review a fresh candidate.",
            )
        # Re-read the chosen manifest; previous candidates may have overwritten the temp file.
        manifest = read_candidate(repo, release, directory, sha, version, code)
        manifest_hash = digest(directory / MANIFEST)
        verify_remote(repo, release, directory, manifest, manifest_hash)
        check_upgrade(repo, manifest, directory, skip=release["id"])
        require(api(f"repos/{repo}/commits/{tag}")["sha"] == sha, "Remote tag moved")
        if release["draft"]:
            api(
                f"repos/{repo}/releases/{release['id']}",
                {
                    "tag_name": tag,
                    "target_commitish": sha,
                    "name": f"Raticode Mobile {version}",
                    "body": release["body"],
                    "draft": False,
                    "prerelease": manifest["prerelease"],
                    # The desktop updater follows this repository's latest release.
                    "make_latest": "false",
                },
                "PATCH",
            )
        result = api(f"repos/{repo}/releases/{release['id']}")
        require(not result["draft"] and result["tag_name"] == tag, "Publication not confirmed")
        verify_remote(repo, result, directory, manifest, manifest_hash)
        print(f"Published and verified {result['html_url']}. No files were rebuilt or replaced.")


def signing_policy() -> bool:
    value = os.environ.get("SIGNED_RELEASE", "false")
    require(value in {"true", "false"}, "SIGNED_RELEASE must be true or false")
    return value == "true"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("versions", help="check version.properties")
    check = commands.add_parser("check-apk", help="check a built release APK against the release policy")
    check.add_argument("--apk", type=Path, help="defaults to the Gradle release output for --signing")
    check.add_argument("--signing", choices=("signed", "unsigned"), required=True)
    check.add_argument("--output", type=Path, help="write the evidence JSON here")
    bundle = commands.add_parser("check-bundle", help="check a built release App Bundle against the release policy")
    bundle.add_argument("--aab", type=Path, default=RELEASE_BUNDLE)
    bundle.add_argument("--signing", choices=("signed", "unsigned"), required=True)
    bundle.add_argument("--output", type=Path, help="write the evidence JSON here")
    gather = commands.add_parser("collect", help="check the release APK and copy it into a candidate folder")
    gather.add_argument("--output", type=Path, default=Path("candidate"))
    scan = commands.add_parser("audit", help="look up release dependencies in OSV")
    scan.add_argument("--inventory", type=Path, default=ROOT / "artifacts/security/resolved-inventory.json")
    scan.add_argument("--output", type=Path, default=Path("candidate/audit-evidence"))
    tests = commands.add_parser("device-test", help="record instrumented results and launch the APK on an emulator")
    tests.add_argument("--candidate", type=Path, default=Path("candidate/android"))
    tests.add_argument("--results", type=Path, help="connectedDebugAndroidTest result folder")
    tests.add_argument("--serial", help="emulator serial; defaults to ANDROID_SERIAL or the only device")
    tests.add_argument("--instrumented-exit", type=int, default=0,
                       help="connectedDebugAndroidTest's exit code; a failure is recorded, not fatal")
    reporting = commands.add_parser("report-instrumented",
                                    help="write the emulator results to the job summary (never fails on them)")
    reporting.add_argument("--candidate", type=Path, default=Path("candidate/android"))
    sealing = commands.add_parser("seal", help="write the checksum file for a candidate folder")
    sealing.add_argument("folder", type=Path)
    for name in ("manifest", "stage", "publish"):
        commands.add_parser(name, help=f"{name} a candidate (GitHub Actions only)")
    args = parser.parse_args(argv)
    try:
        if args.command == "versions":
            version, code = versions()
            print(f"version.properties is valid: {version} ({code})")
        elif args.command == "check-apk":
            signed = args.signing == "signed"
            apk = args.apk or (RELEASE_APK if signed else UNSIGNED_APK)
            evidence = check_apk(apk, signed)
            if args.output:
                args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
            print(f"{apk.name}: {evidence['version']} ({evidence['version_code']}), "
                  f"{'signed ' + evidence['certificate_sha256'] if signed else 'unsigned'}, "
                  f"sha256 {evidence['apk_sha256']}; release policy checks passed")
        elif args.command == "check-bundle":
            evidence = check_bundle(args.aab, args.signing == "signed")
            if args.output:
                args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
            print(f"{args.aab.name}: {evidence['version']} ({evidence['version_code']}), "
                  f"{'signed ' + evidence['certificate_sha256'] if evidence['signed'] else 'unsigned'}, "
                  f"{evidence['aab_size']} bytes, largest phone download at most "
                  f"{evidence['download']['largest_download_estimate']} bytes; release policy checks passed")
        elif args.command == "collect":
            print(f"Collected {collect(args.output, signing_policy())}")
        elif args.command == "audit":
            report = audit(args.inventory, args.output)
            print(f"OSV: {report['checked']} release dependencies, {len(report['findings'])} with advisories, none unreviewed")
        elif args.command == "device-test":
            evidence = device_test(args.candidate, args.results, args.serial, args.instrumented_exit)
            print(f"Device tests recorded: {json.dumps({k: evidence[k] for k in ('instrumented', 'launch')})}")
        elif args.command == "report-instrumented":
            report_instrumented(args.candidate)
        elif args.command == "seal":
            seal(args.folder)
        else:
            repo = os.environ["GITHUB_REPOSITORY"]
            require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "Invalid repository")
            if args.command in {"manifest", "stage"}:
                require(
                    os.environ["GITHUB_REF"] == f"refs/heads/{CANDIDATE_BRANCH}",
                    "Candidates require the trusted stage branch",
                )
            if args.command == "manifest":
                run_id = int(os.environ["GITHUB_RUN_ID"])
                create_manifest(
                    Path("candidate-input"),
                    Path("release-artifacts"),
                    repo,
                    os.environ["GITHUB_SHA"],
                    run_id,
                    int(os.environ["GITHUB_RUN_ATTEMPT"]),
                    pages(f"repos/{repo}/actions/runs/{run_id}/artifacts", "artifacts"),
                    signed=signing_policy(),
                )
            elif args.command == "stage":
                stage(repo, Path("release-artifacts"))
            else:
                publish(repo)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
