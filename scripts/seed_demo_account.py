#!/usr/bin/env python3
"""
Seed Demo Account (tmp@gmail.com) with realistic temporary data.
Populates personal expenses, shadow friends, 1:1 friend expenses, groups, and group expenses
across August and September 2026.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


class APIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None

    def request(
        self, method: str, path: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req) as resp:
                resp_bytes = resp.read()
                if not resp_bytes:
                    return {}
                return json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            print(f"HTTPError {e.code} on {method} {path}: {err_body}", file=sys.stderr)
            raise

    def login(self, email: str, password: str) -> None:
        res = self.request("POST", "/api/v1/auth/login", {"email": email, "password": password})
        data = res.get("data", {})
        self.token = data.get("access_token")
        # In this backend, user object has user_id or id
        user_info = data.get("user", {})
        self.user_id = user_info.get("user_id") or user_info.get("id")
        print(f"✓ Logged in as {email} (User ID: {self.user_id})")


def seed_demo_data(base_url: str, email: str, password: str, clean: bool = False) -> None:
    client = APIClient(base_url)
    client.login(email, password)
    tmp_user_id = client.user_id
    assert tmp_user_id, "Could not obtain user_id from login"

    print("\n--- Step 1: Ensure Custom Categories ---")
    cats_res = client.request("GET", "/api/v1/categories/")
    existing_cats = {c.get("slug"): c.get("id") for c in cats_res.get("data", [])}
    if "entertainment" not in existing_cats:
        try:
            new_cat = client.request("POST", "/api/v1/categories/", {"name": "Entertainment"})
            print(f"✓ Created custom category 'Entertainment' (slug: {new_cat.get('data', {}).get('slug')})")
        except Exception as e:
            print(f"! Custom category 'Entertainment' notice: {e}")
    else:
        print("✓ Category 'entertainment' already exists.")

    print("\n--- Step 2: Seed Personal Expenses ---")
    personal_expenses = [
        {"amount": 380.00, "category": "food", "note": "Blue Tokai Coffee & Croissant", "expense_date": "2026-09-11"},
        {"amount": 500.00, "category": "transport", "note": "Metro Smart Card Auto-recharge", "expense_date": "2026-09-10"},
        {"amount": 1450.00, "category": "food", "note": "Nature's Basket Weekly Groceries", "expense_date": "2026-09-08"},
        {"amount": 999.00, "category": "bills", "note": "Airtel Xstream Fiber Broadband", "expense_date": "2026-09-06"},
        {"amount": 850.00, "category": "entertainment", "note": "IMAX Movie Tickets & Snacks", "expense_date": "2026-09-04"},
        {"amount": 2499.00, "category": "shopping", "note": "Decathlon Running Gear", "expense_date": "2026-09-02"},
        {"amount": 1850.00, "category": "bills", "note": "Torrent Power Electricity Bill", "expense_date": "2026-08-28"},
        {"amount": 2200.00, "category": "food", "note": "Mainland China Family Dinner", "expense_date": "2026-08-24"},
        {"amount": 1120.00, "category": "shopping", "note": "Crossword Book Purchase", "expense_date": "2026-08-15"},
    ]

    # Check existing personal expenses so we avoid re-adding identical notes
    existing_exp_res = client.request("GET", "/api/v1/expenses/personal")
    existing_notes = {e.get("note") for e in existing_exp_res.get("data", []) if e.get("note")}

    for exp in personal_expenses:
        if exp["note"] in existing_notes:
            print(f"- Personal expense '{exp['note']}' already exists, skipping.")
            continue
        client.request("POST", "/api/v1/expenses/personal", exp)
        print(f"✓ Added personal expense: {exp['note']} (₹{exp['amount']}, {exp['expense_date']})")

    print("\n--- Step 3: Seed Shadow Friends ---")
    friends_to_create = [
        {"display_name": "Priya Sharma", "email": "priya.sharma@demo.com"},
        {"display_name": "Rohan Mehta", "email": "rohan.mehta@demo.com"},
        {"display_name": "Vikram Patel", "email": "vikram.patel@demo.com"},
    ]

    # Query current friends
    friends_list_res = client.request("GET", "/api/v1/friends/")
    friends_map: Dict[str, str] = {} # email/name -> user_id
    for f in friends_list_res.get("data", []):
        prof = f.get("profile") or {}
        p_email = prof.get("email")
        p_name = prof.get("display_name")
        p_uid = prof.get("user_id") or f.get("friend_id")
        if p_email:
            friends_map[p_email] = p_uid
        if p_name:
            friends_map[p_name] = p_uid

    for f_data in friends_to_create:
        if f_data["email"] in friends_map:
            print(f"- Shadow friend '{f_data['display_name']}' already exists (User ID: {friends_map[f_data['email']]})")
            continue
        try:
            shadow_res = client.request("POST", "/api/v1/friends/shadow", f_data)
            data = shadow_res.get("data", {})
            profile = data.get("profile", {})
            f_uid = profile.get("user_id") or data.get("shadow_user_id")
            friends_map[f_data["email"]] = f_uid
            friends_map[f_data["display_name"]] = f_uid
            print(f"✓ Created shadow friend '{f_data['display_name']}' (User ID: {f_uid})")
        except Exception as e:
            print(f"! Error creating shadow friend {f_data['display_name']}: {e}")

    priya_id = friends_map.get("priya.sharma@demo.com") or friends_map.get("Priya Sharma")
    rohan_id = friends_map.get("rohan.mehta@demo.com") or friends_map.get("Rohan Mehta")
    vikram_id = friends_map.get("vikram.patel@demo.com") or friends_map.get("Vikram Patel")

    print("\n--- Step 4: Seed 1:1 Friend Expenses ---")
    friend_expenses = []
    if priya_id:
        friend_expenses.extend([
            {
                "friend_id": priya_id,
                "amount": 2400.00,
                "category": "food",
                "note": "Burma Burma Dinner",
                "expense_date": "2026-09-09",
                "paid_by": tmp_user_id,
                "splits": [
                    {"user_id": tmp_user_id, "amount": 1200.00},
                    {"user_id": priya_id, "amount": 1200.00},
                ],
            },
            {
                "friend_id": priya_id,
                "amount": 800.00,
                "category": "transport",
                "note": "Airport Uber Ride",
                "expense_date": "2026-09-07",
                "paid_by": priya_id,
                "splits": [
                    {"user_id": tmp_user_id, "amount": 400.00},
                    {"user_id": priya_id, "amount": 400.00},
                ],
            },
        ])

    if rohan_id:
        friend_expenses.append({
            "friend_id": rohan_id,
            "amount": 3000.00,
            "category": "entertainment",
            "note": "Prateek Kuhad Concert Tickets",
            "expense_date": "2026-09-05",
            "paid_by": rohan_id,
            "splits": [
                {"user_id": tmp_user_id, "amount": 1500.00},
                {"user_id": rohan_id, "amount": 1500.00},
            ],
        })

    if vikram_id:
        friend_expenses.append({
            "friend_id": vikram_id,
            "amount": 1200.00,
            "category": "other",
            "note": "Board Game Cafe Evening",
            "expense_date": "2026-09-03",
            "paid_by": tmp_user_id,
            "splits": [
                {"user_id": tmp_user_id, "amount": 600.00},
                {"user_id": vikram_id, "amount": 600.00},
            ],
        })

    for fe in friend_expenses:
        f_id = fe.pop("friend_id")
        try:
            # Check if this expense already exists
            existing_f_exp = client.request("GET", f"/api/v1/friends/{f_id}/expenses")
            existing_notes_f = {e.get("note") for e in existing_f_exp.get("data", [])}
            if fe["note"] in existing_notes_f:
                print(f"- Friend expense '{fe['note']}' already exists, skipping.")
                continue
            client.request("POST", f"/api/v1/friends/{f_id}/expenses", fe)
            print(f"✓ Added 1:1 friend expense: {fe['note']} (₹{fe['amount']})")
        except Exception as e:
            print(f"! Failed to add friend expense '{fe['note']}': {e}")

    print("\n--- Step 5: Seed Groups and Group Expenses ---")
    groups_res = client.request("GET", "/api/v1/groups/")
    existing_groups = {g.get("name"): g for g in groups_res.get("data", [])}

    # Group 1: Apartment 402
    apt_group = existing_groups.get("Apartment 402")
    if not apt_group:
        res = client.request("POST", "/api/v1/groups/", {
            "name": "Apartment 402",
            "type": "home",
            "description": "Monthly flatmate expenses",
        })
        apt_group = res.get("data", {})
        print(f"✓ Created Group 'Apartment 402' (ID: {apt_group.get('id')})")
    else:
        print(f"- Group 'Apartment 402' already exists (ID: {apt_group.get('id')})")

    apt_group_id = apt_group.get("id")
    if apt_group_id:
        # Add members to Apartment 402
        members_res = client.request("GET", f"/api/v1/groups/{apt_group_id}/members")
        current_members = {m.get("user_id") for m in members_res.get("data", [])}
        for mem_id, mem_email in [(priya_id, "priya.sharma@demo.com"), (rohan_id, "rohan.mehta@demo.com")]:
            if mem_id and mem_id not in current_members:
                try:
                    client.request("POST", f"/api/v1/groups/{apt_group_id}/members", {
                        "user_id": mem_id,
                        "email": mem_email,
                        "role": "member",
                    })
                    print(f"✓ Added {mem_email} to 'Apartment 402'")
                except Exception as e:
                    print(f"! Error adding {mem_email} to group: {e}")

        # Check existing group expenses
        g_exp_res = client.request("GET", f"/api/v1/groups/{apt_group_id}/expenses")
        existing_g_notes = {e.get("note") for e in g_exp_res.get("data", [])}

        apt_expenses = [
            {
                "amount": 3600.00,
                "category": "bills",
                "note": "Society Maintenance & Wi-Fi",
                "expense_date": "2026-09-01",
                "paid_by": tmp_user_id,
            },
            {
                "amount": 2100.00,
                "category": "food",
                "note": "Monthly Kitchen Pantry Essentials",
                "expense_date": "2026-09-07",
                "paid_by": priya_id,
            },
        ]
        for exp in apt_expenses:
            if exp["note"] in existing_g_notes:
                print(f"- Group expense '{exp['note']}' already exists, skipping.")
                continue
            try:
                client.request("POST", f"/api/v1/groups/{apt_group_id}/expenses", exp)
                print(f"✓ Added group expense to Apartment 402: {exp['note']} (₹{exp['amount']})")
            except Exception as e:
                print(f"! Error adding group expense '{exp['note']}': {e}")

    # Group 2: Goa Getaway
    goa_group = existing_groups.get("Goa Getaway")
    if not goa_group:
        res = client.request("POST", "/api/v1/groups/", {
            "name": "Goa Getaway",
            "type": "trip",
            "description": "Weekend beach trip with friends",
        })
        goa_group = res.get("data", {})
        print(f"✓ Created Group 'Goa Getaway' (ID: {goa_group.get('id')})")
    else:
        print(f"- Group 'Goa Getaway' already exists (ID: {goa_group.get('id')})")

    goa_group_id = goa_group.get("id")
    if goa_group_id:
        members_res = client.request("GET", f"/api/v1/groups/{goa_group_id}/members")
        current_members = {m.get("user_id") for m in members_res.get("data", [])}
        for mem_id, mem_email in [(vikram_id, "vikram.patel@demo.com"), (priya_id, "priya.sharma@demo.com")]:
            if mem_id and mem_id not in current_members:
                try:
                    client.request("POST", f"/api/v1/groups/{goa_group_id}/members", {
                        "user_id": mem_id,
                        "email": mem_email,
                        "role": "member",
                    })
                    print(f"✓ Added {mem_email} to 'Goa Getaway'")
                except Exception as e:
                    print(f"! Error adding {mem_email} to group: {e}")

        g_exp_res = client.request("GET", f"/api/v1/groups/{goa_group_id}/expenses")
        existing_g_notes = {e.get("note") for e in g_exp_res.get("data", [])}

        goa_expenses = [
            {
                "amount": 15000.00,
                "category": "other",
                "note": "Anjuna Beachside Villa Booking",
                "expense_date": "2026-08-25",
                "paid_by": tmp_user_id,
            },
            {
                "amount": 4500.00,
                "category": "food",
                "note": "Fisherman's Wharf Seafood Dinner",
                "expense_date": "2026-08-26",
                "paid_by": vikram_id,
            },
        ]
        for exp in goa_expenses:
            if exp["note"] in existing_g_notes:
                print(f"- Group expense '{exp['note']}' already exists, skipping.")
                continue
            try:
                client.request("POST", f"/api/v1/groups/{goa_group_id}/expenses", exp)
                print(f"✓ Added group expense to Goa Getaway: {exp['note']} (₹{exp['amount']})")
            except Exception as e:
                print(f"! Error adding group expense '{exp['note']}': {e}")

    print("\n--- Step 6: Summary Verification ---")
    summary_res = client.request("GET", "/api/v1/dashboard/summary")
    summary = summary_res.get("data", {})
    print("Dashboard Summary:")
    print(f"  Personal Total (Current Month): ₹{summary.get('personalTotal')}")
    print(f"  Group Share Total (Current Month): ₹{summary.get('groupShareTotal')}")
    print(f"  Unified Total: ₹{summary.get('unifiedTotal')}")
    print(f"  Net Balance: ₹{summary.get('netBalance')} (Owed: ₹{summary.get('netOwed')}, Owes: ₹{summary.get('netOwes')})")
    print(f"  Recent Activity Count: {len(summary.get('recentActivity', []))}")
    print("\n✓ Seed completed successfully!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo account tmp@gmail.com with test data")
    parser.add_argument("--base-url", default="https://65.1.93.155.sslip.io", help="Backend API base URL")
    parser.add_argument("--email", default="tmp@gmail.com", help="User email")
    parser.add_argument("--password", default="123123", help="User password")
    parser.add_argument("--clean", action="store_true", help="Clean up existing seeded records first")
    args = parser.parse_args()

    seed_demo_data(args.base_url, args.email, args.password, clean=args.clean)


if __name__ == "__main__":
    main()
