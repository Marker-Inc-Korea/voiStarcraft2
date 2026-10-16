from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest

from starcraft_commander.companion_ui import render_companion_page


class CompanionDirectFeedbackTest(unittest.TestCase):
    """Keep Direct clarification visible in the compact companion contract."""

    def test_repair_clarification_is_rendered_and_focuses_safe_reply_input(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")

        page = render_companion_page()
        script_start = page.index('  "use strict";')
        script_end = page.index(
            '  document.getElementById("runtime-start")',
            script_start,
        )
        command_script = page[script_start:script_end]
        harness = r"""
const assert = require("assert");

class FakeElement {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.textContent = "";
    this.value = "";
    this.focusCount = 0;
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    this.children.splice(this.children.indexOf(child), 1);
  }
  focus() {
    this.focusCount += 1;
    global.document.activeElement = this;
  }
  get firstChild() {
    return this.children[0] || null;
  }
}

const nodes = {};
[
  "caption-list", "chat-list", "command-feedback", "command-input",
  "operation-composition", "operation-goal", "operation-stage",
  "queue-status", "runtime-status"
].forEach(function(id) { nodes[id] = new FakeElement(); });
global.document = {
  activeElement: null,
  createElement: function() { return new FakeElement(); },
  getElementById: function(id) { return nodes[id]; }
};
global.window = {
  location: { search: "" },
  setTimeout: function() { return 1; },
  clearTimeout: function() {}
};
"""
        scenario = r"""
(function() {
  renderRuntime({
    status: "connected",
    runtime_attached: true,
    telemetry_current_for_process: true,
    telemetry_frame: 123
  });
  const prompt = "여러 손상 대상이 있습니다. 어느 벙커를 수리할까요?";
  renderOperation({
    status: "clarification_required",
    latest_request: {
      update_id: "repair-clarify-1",
      command_text: "SCV로 손상된 벙커를 수리해",
      status: "clarification_required",
      clarification_prompt: prompt,
      consumption_status: "not_published"
    },
    compile_result: {
      update_id: "repair-clarify-1",
      status: "clarification_required",
      clarification_prompt: prompt,
      command_text: "SCV로 손상된 벙커를 수리해"
    },
    operations: []
  });
  assert.strictEqual(nodes["operation-stage"].textContent, "확인 필요");
  assert.strictEqual(nodes["command-feedback"].style.display, "block");
  assert.ok(nodes["command-feedback"].textContent.indexOf(prompt) !== -1);
  assert.ok(nodes["caption-list"].children.some(function(node) {
    return node.textContent.indexOf(prompt) !== -1;
  }));
  assert.ok(nodes["chat-list"].children.some(function(node) {
    return node.children[1].textContent === prompt;
  }));
  assert.strictEqual(nodes["command-input"].focusCount, 1);
  // The UI must show the semantic prompt, never adapter-private raw tags.
  assert.strictEqual(nodes["command-feedback"].textContent.indexOf("owned_unit_tags"), -1);

  const compileOnly = {
    status: "clarification_required",
    latest_request: { update_id: "repair-clarify-2" },
    compile_result: {
      update_id: "repair-clarify-2",
      command_text: "벙커 수리해",
      clarification_prompt: prompt
    }
  };
  renderOperation(compileOnly);
  const captionCount = nodes["caption-list"].children.length;
  renderOperation(compileOnly);
  assert.strictEqual(nodes["operation-stage"].textContent, "확인 필요");
  assert.strictEqual(nodes["caption-list"].children.length, captionCount);
  assert.strictEqual(nodes["command-input"].focusCount, 1);

  // A stale compiler result must not leak via its nested queue or command
  // text after a different request has become current, in either branch.
  [[], [{ update_id: "new-move", active: true }]].forEach(function(operations) {
    const selected = selectCommand({
      latest_request: { update_id: "new-move" },
      compile_result: {
        update_id: "old-repair",
        command_text: "OLD REPAIR",
        clarification_prompt: prompt,
        command_queue: { clarification_prompt: prompt }
      },
      operations: operations
    });
    assert.strictEqual(operationClarificationPrompt(selected), "");
    assert.notStrictEqual(operationStage(selected, {}), "확인 필요");
    assert.notStrictEqual(selected.command_text, "OLD REPAIR");
  });
})();
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js") as script_file:
            script_file.write(harness)
            script_file.write(command_script)
            script_file.write(scenario)
            script_file.flush()
            result = subprocess.run(
                [node, script_file.name],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
