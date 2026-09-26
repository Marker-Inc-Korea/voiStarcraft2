#pragma once

#include <sstream>
#include <string>

struct VoiOperationCompletionObservation
{
    bool orderIssued = false;
    bool targetReached = false;
    bool enemyObserved = false;
    bool abilityObserved = false;
    bool retreatConfirmed = false;
};

inline bool voiOperationObjectivesSatisfied(
    const std::string & conditions,
    const VoiOperationCompletionObservation & observed)
{
    std::istringstream input(conditions);
    std::string condition;
    bool hasObjective = false;
    while (std::getline(input, condition, ','))
    {
        // Cancellation and expiry are alternative exits, not successful goals.
        if (condition == "cancelled_by_user" || condition == "ttl_expired")
            continue;
        bool satisfied = false;
        if (condition == "order_issued")
            satisfied = observed.orderIssued;
        else if (condition == "target_reached")
            satisfied = observed.targetReached;
        else if (condition == "enemy_observed")
            satisfied = observed.enemyObserved;
        else if (condition == "ability_cast")
            satisfied = observed.abilityObserved;
        else if (condition == "retreat_confirmed")
            satisfied = observed.retreatConfirmed;
        else
            return false;
        hasObjective = true;
        if (!satisfied)
            return false;
    }
    return hasObjective;
}
