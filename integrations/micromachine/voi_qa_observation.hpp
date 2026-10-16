#pragma once

#include <sc2api/sc2_api.h>
#include <sstream>
#include <string>

// Raw observations, separate from manager claims. Only used by opt-in live QA.
inline std::string voiQaObservationJson(const sc2::ObservationInterface & observation)
{
    std::ostringstream out;
    const auto & score = observation.GetScore().score_details;
    out << "{\"schema_version\":1,\"source\":\"sc2_observation\",\"frame\":"
        << observation.GetGameLoop()
        << ",\"collected_minerals\":" << score.collected_minerals
        << ",\"collected_vespene\":" << score.collected_vespene
        << ",\"collection_rate_minerals\":" << score.collection_rate_minerals
        << ",\"collection_rate_vespene\":" << score.collection_rate_vespene
        << ",\"minerals\":" << observation.GetMinerals()
        << ",\"vespene\":" << observation.GetVespene()
        << ",\"units\":[";
    bool first = true;
    for (const sc2::Unit * unit : observation.GetUnits())
    {
        if (unit == nullptr || !unit->is_alive
            || (unit->alliance != sc2::Unit::Self
                && unit->display_type != sc2::Unit::Visible))
            continue;
        if (!first) out << ",";
        first = false;
        out << "{\"tag\":" << unit->tag
            << ",\"type\":\"" << sc2::UnitTypeToName(unit->unit_type) << "\""
            << ",\"alliance\":" << static_cast<int>(unit->alliance)
            << ",\"build_progress\":" << unit->build_progress
            << ",\"x\":" << unit->pos.x << ",\"y\":" << unit->pos.y
            << ",\"health\":" << unit->health << ",\"shield\":" << unit->shield
            << ",\"flying\":" << (unit->is_flying ? "true" : "false")
            << ",\"add_on_tag\":" << unit->add_on_tag
            << ",\"assigned_harvesters\":" << unit->assigned_harvesters
            << ",\"ideal_harvesters\":" << unit->ideal_harvesters
            << ",\"engaged_target_tag\":" << unit->engaged_target_tag
            << ",\"mineral_contents\":" << unit->mineral_contents
            << ",\"vespene_contents\":" << unit->vespene_contents
            << ",\"orders\":[";
        bool firstOrder = true;
        for (const auto & order : unit->orders)
        {
            if (!firstOrder) out << ",";
            firstOrder = false;
            out << "{\"ability\":" << static_cast<uint32_t>(order.ability_id)
                << ",\"ability_name\":\"" << sc2::AbilityTypeToName(order.ability_id) << "\""
                << ",\"target_tag\":" << order.target_unit_tag
                << ",\"x\":" << order.target_pos.x << ",\"y\":" << order.target_pos.y
                << ",\"progress\":" << order.progress << "}";
        }
        out << "]}";
    }
    out << "]}";
    return out.str();
}
