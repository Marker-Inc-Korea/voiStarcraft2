"use strict";
function validateCommand(input){if(!input||typeof input!=="object"||Array.isArray(input))throw new TypeError("command must be an object");const text=typeof input.text==="string"?input.text.trim():"";if(!text||text.length>2000)throw new TypeError("command text must contain 1..2000 characters");return {text,ui_language:"ko",response_language:input.response_language==="en"?"en":"ko"};}
module.exports={validateCommand};
