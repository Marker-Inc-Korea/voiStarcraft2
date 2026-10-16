const test=require("node:test");const assert=require("node:assert/strict");const {validateCommand}=require("../protocol.js");
test("command IPC accepts bounded semantic text",()=>assert.deepEqual(validateCommand({text:"앞마당 방어"}),{text:"앞마당 방어",ui_language:"ko",response_language:"ko"}));
test("command IPC rejects empty or oversized input",()=>{assert.throws(()=>validateCommand({text:""}),/1..2000/);assert.throws(()=>validateCommand({text:"x".repeat(2001)}),/1..2000/);});
