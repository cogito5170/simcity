
// ---- simcity AI mayor additions (GPL-3.0-or-later, like the rest of this file) ----
// Department funding control: the engine already has road/police/fire percentages
// (budget.cpp doBudgetNow) but the C API did not expose them.
extern "C" {
void mp_set_budget(Micropolis *sim, int road_pct, int police_pct, int fire_pct)
{
    auto clamp01 = [](int p) { return (float)(p < 0 ? 0 : (p > 100 ? 100 : p)) / 100.0f; };
    sim->roadPercent = clamp01(road_pct);
    sim->policePercent = clamp01(police_pct);
    sim->firePercent = clamp01(fire_pct);
}

// The original UI called updateFundEffects() after the yearly budget dialog; headless builds
// never call it, so funding shortfalls had no effect on roads, police or fire. Call this after
// each month to apply road decay / coverage loss the way the game does.
void mp_update_fund_effects(Micropolis *sim)
{
    sim->updateFundEffects();
}

// out[0..7]: road %, police %, fire %, road fund, police fund, fire fund, tax fund, auto budget (0/1)
void mp_budget_info(Micropolis *sim, double *out)
{
    out[0] = sim->roadPercent * 100.0;
    out[1] = sim->policePercent * 100.0;
    out[2] = sim->firePercent * 100.0;
    out[3] = (double)sim->roadFund;
    out[4] = (double)sim->policeFund;
    out[5] = (double)sim->fireFund;
    out[6] = (double)sim->taxFund;
    out[7] = sim->autoBudget ? 1.0 : 0.0;
}
}  // extern "C"
