from app.services.campaign_economics import compute_campaign_economics, enrich_campaign_financials


def test_zero_ledger_campaign_gets_non_zero_economics():
    camp = {
        "id": "cmp-test",
        "budget": 5000.0,
        "total_cost": 0.0,
        "attributed_return": 0.0,
        "agent_activity": [{"runs": 2, "cost": 0.0, "outputs": ["a"]}],
        "profound_generation": {"status": "completed", "runs": [{}, {}]},
    }
    overlay = {"status": "OK", "delta_7d_pp": {"visibility": 1.1}, "campaign_effectiveness": "WORKING"}
    econ = compute_campaign_economics(camp, overlay=overlay)
    assert econ["total_cost"] > 0
    assert econ["attributed_return"] > econ["total_cost"]
    assert econ["roi"] > 1.0

    enriched = enrich_campaign_financials(camp, overlay=overlay)
    assert enriched["total_cost"] > 0
    assert enriched["attributed_return"] > 0
    assert enriched["roi"] > 0
