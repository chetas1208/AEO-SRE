# Third-party references (A9)

| Reference | Version / SHA | License | Code reused | Use |
|---|---|---|---|---|
| Open Bandit Pipeline (`obp`, st-tech/zr-obp), pip package | 0.4.1 | Apache-2.0 (LICENSE read at https://github.com/st-tech/zr-obp, master) | NO. Algorithms and estimators were written from the published ideas (LinUCB, Li et al. 2010; IPS/SNIPS/DR). | Installed ONLY in a separate throwaway venv (python 3.11, pandas<2) as an independent reference in `backend/app/learning/obp_validation.py`. Not a backend dependency. |
| Open Bandit Dataset, random-policy "men" sample (10k rows, bundled with the obp package) | obp 0.4.1 | CC BY 4.0 (ZOZO) / package Apache-2.0 | data only, not stored in repo | Validation only. NOT used to train or initialise the AEO policy. No domain transfer to AEO. |
