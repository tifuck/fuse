const { chmod } = require("fs/promises");
const path = require("path");

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "linux") return;
  await chmod(path.join(context.appOutDir, "chrome-sandbox"), 0o4755);
};
