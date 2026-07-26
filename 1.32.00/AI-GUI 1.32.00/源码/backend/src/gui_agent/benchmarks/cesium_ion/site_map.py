"""Read-only Cesium ion page map observed on 2026-07-22."""

from __future__ import annotations


PAGES = [
    {"route": "/stories", "title": "Stories", "shadowHosts": [], "controls": ["New story", "Search for..."], "observedState": "0 stories", "writeControls": ["New story"]},
    {"route": "/assets", "title": "My Assets", "shadowHosts": ["ion-assets-page"], "controls": ["Add data", "type filter", "date filter", "Search for...", "sorting", "pagination", "row selection"], "observedState": "11 existing non-E2E assets; 0.00 GiB / 5.00 GiB", "writeControls": ["Add data", "Delete"]},
    {"route": "/addasset", "title": "Add Data", "shadowHosts": ["ion-azure-upload-form"], "controls": ["hidden multiple file input", "S3", "Azure", "Sketchfab"], "observedState": "no file selected", "writeControls": ["Upload", "cloud import"]},
    {"route": "/assetdepot", "title": "Asset Depot", "shadowHosts": [], "controls": ["Search for...", "Name", "Description", "Type", "Add", "Add to my assets"], "observedState": "catalog and detail available", "writeControls": ["Add to my assets"]},
    {"route": "/clips", "title": "Clips", "shadowHosts": ["ion-clips-page"], "controls": ["Create clip", "Search for...", "ID", "Status", "Size", "Date", "Quota", "pagination"], "observedState": "0 of 10 Asset Depot clips used", "writeControls": ["Create clip"]},
    {"route": "/tokens", "title": "Access Tokens", "shadowHosts": [], "controls": ["Create token", "Search for...", "Name", "Last used", "Scopes", "Regenerate"], "observedState": "1 default token; value intentionally not captured", "writeControls": ["Create token", "Regenerate"]},
    {"route": "/usage", "title": "Usage", "shadowHosts": ["ion-analyze-usage-details"], "controls": ["token filter", "Week", "Month", "Year", "Custom", "from", "to"], "observedState": "week: 11.52 MiB streaming, 1 Bing imagery session, 3 Google 3D sessions, 0 geocodes, 0 analysis minutes", "writeControls": []},
    {"route": "/account", "title": "Account", "shadowHosts": [], "controls": ["username", "email", "data center", "password", "2FA", "alternate sign in"], "observedState": "field names only; values not captured", "writeControls": ["Update", "Link", "2FA", "Delete account"]},
    {"route": "/account/billing", "title": "Billing", "shadowHosts": [], "controls": ["Plan Limits", "Upgrade", "Billing History"], "observedState": "Community; no billing history", "writeControls": ["Upgrade"]},
    {"route": "/account/license", "title": "License", "shadowHosts": [], "controls": ["Terms of Service"], "observedState": "accepted terms visible", "writeControls": []},
    {"route": "/account/labels", "title": "Labels", "shadowHosts": [], "controls": ["New label", "Search for...", "asset/story counts", "Delete", "Edit"], "observedState": "existing favorite label with 0 assets and 0 stories", "writeControls": ["New label", "Delete", "Edit"]},
    {"route": "/account/applications", "title": "Authorized Applications", "shadowHosts": [], "controls": ["Search for...", "Application", "Last Used", "Delete"], "observedState": "no applications", "writeControls": ["Delete"]},
    {"route": "/account/developer", "title": "OAuth Applications", "shadowHosts": [], "controls": ["Add Application", "Search for...", "OAuth Tutorial"], "observedState": "no OAuth applications", "writeControls": ["Add Application"]},
    {"route": "/account/teams", "title": "Teams", "shadowHosts": ["ion-teams-page"], "controls": ["Create a team", "Name", "Actions"], "observedState": "no teams", "writeControls": ["Create a team"]},
]


def site_map_payload() -> dict:
    return {
        "version": "1.32.00",
        "target": "https://ion.cesium.com",
        "inspectedAt": "2026-07-22",
        "accountContext": "authenticated personal account; username and secrets excluded",
        "pages": PAGES,
        "globalNavigation": ["Stories", "My Assets", "Asset Depot", "Clips", "Access Tokens", "Usage"],
        "accountNavigation": ["Account", "Billing", "License", "Labels", "Authorized Applications", "Developer Settings", "Teams", "Sign Out"],
        "safety": {"existingAssetsAreE2EOwned": False, "defaultTokenMutable": False, "billingWritesAllowed": False},
    }

