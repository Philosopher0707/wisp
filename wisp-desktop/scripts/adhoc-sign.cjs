// electron-builder afterPack hook: ad-hoc sign the whole bundle (including the bundled Python) so the seal is valid.
// Without it the app's resources are not covered by its signature and `codesign --verify` fails. Ad-hoc is not a
// Developer ID: other Macs still show the Gatekeeper prompt (right-click > Open) until the app is signed and notarized.
const { execFileSync } = require('node:child_process');
const path = require('node:path');

exports.default = async function adhocSign(context) {
  if (context.electronPlatformName !== 'darwin') return;
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', app], { stdio: 'inherit' });
  execFileSync('codesign', ['--verify', '--deep', '--strict', app], { stdio: 'inherit' });
};
