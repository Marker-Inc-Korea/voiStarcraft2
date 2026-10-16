#pragma once

// Runtime-independent bridge for terminal Direct lease callbacks.
// The Python executor writes one URL-safe key=value record per callback:
// protocol_version=voi-mm-bridge/v1;command_id=...;state=completed;
// release_reason=completion_conditions;runtime_instance_id=...
// This deliberately avoids a JSON dependency in MicroMachine's game loop.

#include <cstdint>
#include <functional>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace voi {

struct DirectLeaseRelease {
    std::string commandId;
    std::string state;
    std::string reason;
    std::string runtimeInstanceId;
    std::uint32_t frame = 0;
};

class DirectLeaseBridge {
public:
    using ReleaseCallback = std::function<void(const DirectLeaseRelease&)>;
    using ResumeCallback = std::function<void()>;

    explicit DirectLeaseBridge(ReleaseCallback release, ResumeCallback resume = {})
        : m_release(std::move(release)), m_resume(std::move(resume)) {}

    // Returns false for malformed, incompatible, or stale records. Replaying
    // an already terminal command is accepted but does not invoke callbacks.
    bool consume(const std::string& record, std::uint32_t currentFrame) {
        const auto fields = parse(record);
        if (!fields.count("protocol_version") || fields.at("protocol_version") != "voi-mm-bridge/v1") {
            m_lastError = "invalid or incompatible lease release record";
            return false;
        }
        const auto command = fields.find("command_id");
        const auto state = fields.find("state");
        if (command == fields.end() || command->second.empty() || state == fields.end()) {
            m_lastError = "lease release requires command_id and state";
            return false;
        }
        if (state->second != "completed" && state->second != "cancelled"
            && state->second != "expired" && state->second != "failed") {
            m_lastError = "lease release state is not terminal";
            return false;
        }
        const auto runtime = fields.find("runtime_instance_id");
        if (runtime == fields.end() || runtime->second.size() != 32
            || runtime->second.find_first_not_of("0123456789abcdef") != std::string::npos
            || m_runtimeInstance.empty() || runtime->second != m_runtimeInstance) {
            m_lastError = "lease release runtime does not match attached runtime";
            return false;
        }
        if (!m_seen.insert(command->second).second) return true;
        DirectLeaseRelease event;
        event.commandId = command->second;
        event.state = state->second;
        event.reason = fields.count("release_reason") ? fields.at("release_reason") : "";
        event.runtimeInstanceId = m_runtimeInstance;
        event.frame = currentFrame;
        if (m_release) m_release(event);
        if (m_resume) m_resume();
        m_lastError.clear();
        return true;
    }

    void reconnect(const std::string& runtimeInstanceId) {
        if (runtimeInstanceId != m_runtimeInstance) m_seen.clear();
        m_runtimeInstance = runtimeInstanceId;
    }
    const std::string& lastError() const { return m_lastError; }
    const std::string& runtimeInstanceId() const { return m_runtimeInstance; }

private:
    static std::unordered_map<std::string, std::string> parse(const std::string& record) {
        std::unordered_map<std::string, std::string> result;
        std::istringstream input(record);
        std::string field;
        while (std::getline(input, field, ';')) {
            const auto split = field.find('=');
            if (split == std::string::npos) continue;
            auto key = field.substr(0, split);
            auto value = field.substr(split + 1);
            if (!key.empty()) result[key] = value;
        }
        return result;
    }
    ReleaseCallback m_release;
    ResumeCallback m_resume;
    std::unordered_set<std::string> m_seen;
    std::string m_runtimeInstance;
    std::string m_lastError;
};

} // namespace voi
