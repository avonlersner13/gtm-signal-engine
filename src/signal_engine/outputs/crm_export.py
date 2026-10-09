"""CRM-ready CSVs (out/crm_accounts.csv, out/crm_contacts.csv).

Column names follow HubSpot internal property names where a standard property exists;
the Salesforce mapping is documented in docs/EVENT_SCHEMA.md and in ACCOUNT_FIELDS /
CONTACT_FIELDS below. Custom fields (pqa_flag, primary_play, fit_score, intent_score,
...) must be created in the CRM before import.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from signal_engine.models import AccountView, WeekResult

# column -> (HubSpot property, Salesforce field)
ACCOUNT_FIELDS: dict[str, tuple[str, str]] = {
    "signal_engine_account_id": (
        "signal_engine_account_id (custom)",
        "Signal_Engine_Id__c (External ID)",
    ),
    "name": ("name", "Account.Name"),
    "domain": ("domain", "Account.Website"),
    "industry": ("industry", "Account.Industry"),
    "numberofemployees": ("numberofemployees", "Account.NumberOfEmployees"),
    "region": ("region__c (custom)", "Region__c"),
    "subsidiaries": ("subsidiaries (custom)", "Child accounts via ParentId"),
    "lifecyclestage": ("lifecyclestage", "Lifecycle_Stage__c"),
    "product_stage": ("product_stage (custom)", "Product_Stage__c"),
    "pqa_flag": ("pqa_flag (custom)", "PQA_Flag__c"),
    "primary_play": ("primary_play (custom)", "Primary_Play__c"),
    "secondary_plays": ("secondary_plays (custom)", "Secondary_Plays__c"),
    "play_owner": ("hubspot_owner_id (map founder/GTM)", "OwnerId"),
    "fit_score": ("fit_score (custom)", "Fit_Score__c"),
    "intent_score": ("intent_score (custom)", "Intent_Score__c"),
    "priority_score": ("priority_score (custom)", "Priority_Score__c"),
    "intent_delta_wow": ("intent_delta_wow (custom)", "Intent_Delta_WoW__c"),
    "seats_used": ("seats_used (custom)", "Seats_Used__c"),
    "trial_days_left": ("trial_days_left (custom)", "Trial_Days_Left__c"),
    "blocked_users": ("blocked_users (custom)", "Blocked_Users__c"),
    "est_arr_usd": ("est_arr_usd (custom)", "Estimated_ARR__c"),
    "enterprise_uplift": ("enterprise_uplift (custom)", "Enterprise_Uplift__c"),
    "churn_risk": ("churn_risk (custom)", "Churn_Risk__c"),
    "top_reason": ("top_reason (custom)", "Top_Reason__c"),
}
CONTACT_FIELDS: dict[str, tuple[str, str]] = {
    "signal_engine_user_id": (
        "signal_engine_user_id (custom)",
        "Signal_Engine_Id__c (External ID)",
    ),
    "email": ("email", "Contact.Email"),
    "firstname": ("firstname", "Contact.FirstName"),
    "lastname": ("lastname", "Contact.LastName"),
    "jobtitle": ("jobtitle", "Contact.Title"),
    "company": ("company", "Contact.AccountId (lookup by Signal_Engine_Id__c)"),
    "signal_engine_account_id": ("associated company (custom key)", "Account.Signal_Engine_Id__c"),
    "persona": ("persona (custom)", "Persona__c"),
    "committee_role": ("committee_role (custom)", "Buying_Role__c"),
    "pql_flag": ("pql_flag (custom)", "PQL_Flag__c"),
    "engaged_flag": ("engaged_flag (custom)", "Engaged_Flag__c"),
    "identity_method": ("identity_method (custom)", "Identity_Method__c"),
    "identity_confidence": ("identity_confidence (custom)", "Identity_Confidence__c"),
}
HUBSPOT_LIFECYCLE = {
    "none": "subscriber",
    "signed_up": "lead",
    "installed": "lead",
    "activated": "marketingqualifiedlead",
    "engaged": "marketingqualifiedlead",
    "pql": "salesqualifiedlead",
    "pqa": "salesqualifiedlead",
    "opportunity": "opportunity",
}


def _csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def exported_accounts(result: WeekResult) -> list[AccountView]:
    """Accounts worth syncing: any product stage or any play."""
    return [a for a in result.accounts if a.stage != "none" or a.plays]


def _account_row(a: AccountView) -> list[object]:
    lifecycle = "customer" if a.seats.paid else HUBSPOT_LIFECYCLE.get(a.stage, "lead")
    primary = a.plays[0] if a.plays else None
    return [
        a.account_id,
        a.name,
        a.domain,
        a.industry,
        a.employee_count,
        a.region,
        "; ".join(a.child_names),
        lifecycle,
        a.stage,
        int(a.pqa),
        primary.play_id if primary else "",
        ";".join(p.play_id for p in a.plays[1:]),
        primary.owner if primary else "",
        a.score.fit,
        a.score.intent,
        a.score.priority,
        a.score.intent_delta,
        a.seats.seats_used,
        a.seats.trial_days_left if a.seats.trial_days_left is not None else "",
        len(a.seats.blocked_users),
        f"{a.revenue.total_arr:.2f}",
        int(a.revenue.enterprise_uplift),
        int(a.churn_risk),
        a.score.reasons[0] if a.score.reasons else "",
    ]


def render_crm_accounts(result: WeekResult) -> str:
    """Accounts CSV."""
    return _csv(list(ACCOUNT_FIELDS), (_account_row(a) for a in exported_accounts(result)))


def _roles(a: AccountView) -> dict[str, str]:
    roles: dict[str, str] = {}
    c = a.committee
    for role, contact in (
        ("economic_buyer", c.economic_buyer),
        ("technical_evaluator", c.technical_evaluator),
        ("champion", c.champion),
    ):
        if contact:
            roles[contact.user_id] = role
    return roles


def render_crm_contacts(result: WeekResult) -> str:
    """Contacts CSV for the exported accounts."""
    rows = []
    for a in exported_accounts(result):
        roles = _roles(a)
        pql, engaged = set(a.pql_users), set(a.engaged_users)
        for p in a.people:
            first, _, last = p.name.partition(" ")
            res = result.resolutions[p.user_id]
            rows.append(
                [
                    p.user_id,
                    p.email,
                    first,
                    last,
                    p.title,
                    a.name,
                    a.account_id,
                    p.persona,
                    roles.get(p.user_id, ""),
                    int(p.user_id in pql),
                    int(p.user_id in engaged),
                    res.method,
                    res.confidence,
                ]
            )
    return _csv(list(CONTACT_FIELDS), rows)
