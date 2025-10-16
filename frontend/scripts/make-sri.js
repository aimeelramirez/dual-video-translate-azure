// node frontend/scripts/make-sri.js frontend/vendor/socket.io.min.js
import fs from "fs";
import crypto from "crypto";
const [,, path] = process.argv;
const b = fs.readFileSync(path);
const hash = crypto.createHash("sha384").update(b).digest("base64");
console.log("sha384-" + hash);
