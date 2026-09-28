const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..", "..");
const read = (relativePath) =>
  fs.readFileSync(path.join(root, relativePath), "utf8");

test("MongoDB bundle copies only the runtime payload", () => {
  const script = read("packaging/bundle_mongodb.ps1");
  const build = read("packaging/build.ps1");

  assert.match(script, /RuntimeFiles\s*=\s*@\([^)]*"mongod\.exe"/is);
  assert.doesNotMatch(script, /RuntimeFiles\s*=\s*@\([^)]*"mongos\.exe"/is);
  assert.match(script, /Copy-Item -LiteralPath/);
  assert.doesNotMatch(script, /Copy-Item -Recurse \(Join-Path \$SourceBin "\*"\)/);
  assert.match(script, /Required MongoDB runtime file missing/);
  for (const legacyFile of ["*.pdb", "mongos.exe", "Install-Compass.ps1"]) {
    assert.match(build, new RegExp(legacyFile.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
});

test("API and watcher use one shared PyInstaller collection", () => {
  const spec = read("packaging/idp_services.spec");
  const build = read("packaging/build.ps1");

  assert.match(spec, /api_a = Analysis\(/);
  assert.match(spec, /watcher_a = Analysis\(/);
  assert.equal((spec.match(/\bCOLLECT\(/g) || []).length, 1);
  assert.match(spec, /name="idp-services"/);
  for (const excluded of ["torch", "torchvision", "torchaudio"]) {
    assert.match(spec, new RegExp(`"${excluded}"`));
  }
  assert.match(build, /idp_services\.spec/);
  assert.doesNotMatch(build, /idp_api\.spec/);
  assert.doesNotMatch(build, /idp_watcher\.spec/);
});

test("launcher and OCR copier target shared services directory", () => {
  const launcher = read("packaging/launcher.py");
  const copier = read("packaging/copy_ocr_runtime.ps1");

  assert.match(launcher, /root \/ "idp-services" \/ "idp-api\.exe"/);
  assert.match(launcher, /root \/ "idp-services" \/ "idp-watcher\.exe"/);
  assert.match(copier, /Join-Path \$DistRoot "idp-services\\_internal"/);
});

test("Inno Setup is per-user and preserves mutable data", () => {
  const installer = read("packaging/installer/IDP-Invoice.iss");

  assert.match(installer, /PrivilegesRequired=lowest/);
  assert.match(installer, /DefaultDirName=\{localappdata\}\\Programs\\IDP Invoice/);
  assert.match(installer, /Source: "\{#DistRoot\}\\\*"/);
  assert.match(installer, /Name: "\{app\}\\data\\db"/);
  assert.match(installer, /Name: "\{app\}\\invoices_data\\to_be_processed"/);
  assert.match(installer, /Name: "\{app\}\\logs"/);
  assert.match(installer, /onlyifdoesntexist/);
  assert.match(installer, /uninsneveruninstall/);
  assert.match(installer, /invoice_count\.enc/);
  assert.match(installer, /license_state\.json/);
  assert.match(installer, /\[InstallDelete\]/);
  for (const immutableTree of ["idp-services", "idp-api", "idp-watcher", "frontend"]) {
    assert.match(installer, new RegExp(`Name: "\\{app\\}\\\\${immutableTree}"`));
  }
});

test("release script builds app then compiles Inno Setup", () => {
  const script = read("packaging/build_installer.ps1");

  assert.match(script, /build\.ps1/);
  assert.match(script, /ISCC\.exe/);
  assert.match(script, /IDP-Invoice\.iss/);
  assert.match(script, /IDP-Invoice-Setup\.exe/);
  for (const requiredPath of [
    "idp-services\\\\_internal",
    "frontend\\\\index.html",
    "tally-bridge\\\\tally-bridge.exe",
    "tally-bridge\\\\.env.example",
    "mongodb\\\\bin\\\\vc_redist.x64.exe",
    ".env.example",
  ]) {
    assert.match(script, new RegExp(requiredPath));
  }
});

test("installer excludes only root mutable directories and requires PaddleOCR data", () => {
  const installer = read("packaging/installer/IDP-Invoice.iss");
  const script = read("packaging/build_installer.ps1");

  for (const mutableDirectory of ["data", "invoices_data", "logs"]) {
    assert.match(
      installer,
      new RegExp(`\\\\${mutableDirectory}\\\\\\*`),
      `${mutableDirectory} exclusion must be anchored to the payload root`,
    );
  }
  assert.doesNotMatch(installer, /(?:^|,)data\\\*/m);
  assert.match(script, /paddleocr\\ppocr\\data\\__init__\.py/);
  assert.match(script, /paddleocr\\ppocr\\data\\imaug\\operators\.py/);
});

test("application build fails fast and pins PyInstaller", () => {
  const build = read("packaging/build.ps1");

  assert.match(build, /npm install[\s\S]*\$LASTEXITCODE[\s\S]*npm install failed/);
  assert.match(build, /npm run build[\s\S]*\$LASTEXITCODE[\s\S]*Frontend build failed/);
  assert.match(build, /PyInstallerVersion\s*=\s*"\d+\.\d+\.\d+"/);
  assert.match(build, /"pyinstaller==\$PyInstallerVersion"/);
  assert.doesNotMatch(build, /pip install --upgrade pyinstaller(?:\s|$)/i);
});
