#pragma once

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_map>

namespace voi {

class PolicyBlackboard {
public:
    bool loadFromFile(const std::string& path) {
        std::ifstream input(path);
        if (!input.good()) {
            m_values.clear();
            m_lastError = "blackboard file not readable: " + path;
            return false;
        }

        std::unordered_map<std::string, std::string> parsed;
        std::string line;
        while (std::getline(input, line)) {
            const auto separator = line.find('=');
            if (separator == std::string::npos) {
                continue;
            }
            auto key = trim(line.substr(0, separator));
            auto value = trim(line.substr(separator + 1));
            if (!key.empty()) {
                parsed[key] = value;
            }
        }

        m_values.swap(parsed);
        m_lastError.clear();
        return true;
    }

    bool empty() const {
        return m_values.empty();
    }

    bool has(const std::string& key) const {
        return m_values.find(key) != m_values.end();
    }

    std::string getString(const std::string& key, const std::string& fallback = "") const {
        const auto found = m_values.find(key);
        return found == m_values.end() ? fallback : found->second;
    }

    float getFloat(const std::string& key, float fallback = 0.0f) const {
        const auto found = m_values.find(key);
        if (found == m_values.end() || found->second.empty()) {
            return fallback;
        }
        try {
            return std::stof(found->second);
        } catch (...) {
            return fallback;
        }
    }

    int getInt(const std::string& key, int fallback = 0) const {
        const auto found = m_values.find(key);
        if (found == m_values.end() || found->second.empty()) {
            return fallback;
        }
        try {
            return std::stoi(found->second);
        } catch (...) {
            return fallback;
        }
    }

    bool getBool(const std::string& key, bool fallback = false) const {
        auto value = lower(getString(key));
        if (value == "true" || value == "1" || value == "yes") {
            return true;
        }
        if (value == "false" || value == "0" || value == "no") {
            return false;
        }
        return fallback;
    }

    bool isExpired(std::uint32_t currentFrame) const {
        const std::string lifetimeMode = getString("lifetime.mode");
        const std::string completionState = getString("lifetime.completion_state", "active");
        if (completionState == "active"
            && (lifetimeMode == "until_cancelled" || lifetimeMode == "standing_order")) {
            return false;
        }
        const int expiresAt = getInt("expires_at_frame", -1);
        return expiresAt >= 0 && currentFrame > static_cast<std::uint32_t>(expiresAt);
    }

    bool isProtocolCompatible() const {
        return getString("protocol_version") == "voi-mm-bridge/v1";
    }

    const std::string& lastError() const {
        return m_lastError;
    }

private:
    static std::string trim(const std::string& value) {
        auto begin = value.begin();
        while (begin != value.end() && std::isspace(static_cast<unsigned char>(*begin))) {
            ++begin;
        }
        auto end = value.end();
        while (end != begin && std::isspace(static_cast<unsigned char>(*(end - 1)))) {
            --end;
        }
        return std::string(begin, end);
    }

    static std::string lower(std::string value) {
        std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
            return static_cast<char>(std::tolower(c));
        });
        return value;
    }

    std::unordered_map<std::string, std::string> m_values;
    std::string m_lastError;
};

}  // namespace voi
