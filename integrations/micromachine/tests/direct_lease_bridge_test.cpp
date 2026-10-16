#include "../voi_direct_lease_bridge.hpp"
#include <cassert>

int main() {
    int releases = 0;
    int resumes = 0;
    std::string state;
    voi::DirectLeaseBridge bridge(
        [&](const voi::DirectLeaseRelease& event) { ++releases; state = event.state; },
        [&]() { ++resumes; });
    bridge.reconnect("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    const std::string done = "protocol_version=voi-mm-bridge/v1;command_id=move-1;state=completed;release_reason=completion_conditions;runtime_instance_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    assert(bridge.consume(done, 100));
    assert(releases == 1 && resumes == 1 && state == "completed");
    assert(bridge.consume(done, 101)); // idempotent replay
    assert(releases == 1);
    assert(!bridge.consume("protocol_version=wrong;command_id=x;state=failed", 2));
    bridge.reconnect("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
    assert(!bridge.consume(done, 200)); // old runtime cannot resume new epoch
    const std::string newDone = "protocol_version=voi-mm-bridge/v1;command_id=move-1;state=completed;runtime_instance_id=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    assert(bridge.consume(newDone, 200));
    assert(releases == 2);
    assert(!bridge.consume("protocol_version=voi-mm-bridge/v1;command_id=x;state=active", 2));
    assert(!bridge.consume("command_id=x;state=failed", 2));
    for (const char* terminal : {"cancelled", "expired", "failed"}) {
        std::string record = "protocol_version=voi-mm-bridge/v1;command_id=";
        record += terminal;
        record += ";state=";
        record += terminal;
        record += ";runtime_instance_id=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        assert(bridge.consume(record, 210));
    }
    assert(releases == 5 && resumes == 5);
    return 0;
}
