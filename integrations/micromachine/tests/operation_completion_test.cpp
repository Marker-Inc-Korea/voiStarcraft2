#include "voi_operation_completion.hpp"

#include <iostream>

int main()
{
    const VoiOperationCompletionObservation moved{true, true, false, false, false};
    const VoiOperationCompletionObservation cast{true, true, false, true, false};
    const VoiOperationCompletionObservation seen{true, true, true, true, false};
    const VoiOperationCompletionObservation retreat{true, false, false, false, true};
    int failed = 0;
    auto check = [&](bool condition, const char * label) {
        if (!condition)
        {
            std::cerr << label << "\n";
            ++failed;
        }
    };
    check(!voiOperationObjectivesSatisfied("target_reached,ability_cast", moved),
          "Arrival must not bypass requested ability effect");
    check(voiOperationObjectivesSatisfied("target_reached,ability_cast", cast),
          "Arrival and observed ability should satisfy the objective");
    check(!voiOperationObjectivesSatisfied("target_reached,enemy_observed", moved),
          "Arrival alone must not prove enemy observation");
    check(voiOperationObjectivesSatisfied("target_reached,enemy_observed", seen),
          "All observed objectives should complete");
    check(!voiOperationObjectivesSatisfied("cancelled_by_user", seen),
          "Cancellation-only orders must not auto-complete");
    check(!voiOperationObjectivesSatisfied("ttl_expired", seen),
          "Expiry-only orders must not auto-complete");
    check(!voiOperationObjectivesSatisfied("", seen), "Empty contract fails closed");
    check(!voiOperationObjectivesSatisfied("building_completed", seen),
          "Unsupported objective fails closed");
    check(!voiOperationObjectivesSatisfied("target_reached,unknown", seen),
          "Unknown clause must not be ignored");
    check(voiOperationObjectivesSatisfied("target_reached,cancelled_by_user", moved),
          "Alternative cancellation must not prevent observed success");
    check(voiOperationObjectivesSatisfied("order_issued", moved),
          "Explicit order-only objective remains supported");
    check(!voiOperationObjectivesSatisfied("retreat_confirmed", moved),
          "Retreat must not be inferred from generic movement");
    check(voiOperationObjectivesSatisfied("retreat_confirmed", retreat),
          "Observed retreat should satisfy the retreat objective");
    return failed ? 1 : 0;
}
