"""
Test script for Phase 6: LLM Order Pipeline.

Tests the full order pipeline end-to-end:
  1. Creates a test user + session + units
  2. Submits orders (RU + EN) via REST API
  3. Checks that the pipeline runs (fallback or LLM)
  4. Verifies parsed orders, unit responses, location resolution

Run:
    python -m scripts.test_order_pipeline
"""

import asyncio
import json
import sys
import httpx

BASE = "http://127.0.0.1:8000"
TIMEOUT = 30.0


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as client:
        print("=" * 70)
        print("  Phase 6 Test: LLM Order Pipeline")
        print("=" * 70)

        # ── 1. Register / Login ──────────────────────────────
        print("\n[1] Registering test user...")
        r = await client.post("/api/auth/register", json={
            "display_name": "TestCmdr", "password": "testtest"
        })
        if r.status_code == 409:
            print("    User exists, logging in...")
            r = await client.post("/api/auth/login", json={
                "display_name": "TestCmdr", "password": "testtest"
            })
        if r.status_code != 200:
            print(f"    ✗ Auth failed: {r.status_code} {r.text}")
            return
        token = r.json()["token"]
        user_id = r.json().get("user", {}).get("id", "?")
        print(f"    ✓ Authenticated (user={user_id[:8]}...)")
        headers = {"Authorization": f"Bearer {token}"}

        # ── 2. Get or create a scenario ──────────────────────
        print("\n[2] Finding scenario...")
        r = await client.get("/api/scenarios", headers=headers)
        scenarios = r.json()
        if not scenarios:
            print("    ✗ No scenarios found. Run 'python -m scripts.seed_scenario' first.")
            return
        scenario = scenarios[0]
        print(f"    ✓ Using scenario: {scenario['title']} ({scenario['id'][:8]}...)")

        # ── 3. Create session ────────────────────────────────
        print("\n[3] Creating session...")
        r = await client.post("/api/sessions", headers=headers, json={
            "scenario_id": scenario["id"],
            "settings": {}
        })
        if r.status_code != 200:
            print(f"    ✗ Session create failed: {r.status_code} {r.text}")
            return
        session = r.json()
        session_id = session["id"]
        print(f"    ✓ Session created: {session_id[:8]}...")

        # ── 4. Join session ──────────────────────────────────
        print("\n[4] Joining session as Blue commander...")
        r = await client.post(f"/api/sessions/{session_id}/join", headers=headers, json={
            "side": "blue", "role": "commander"
        })
        if r.status_code not in (200, 400, 409):
            print(f"    ✗ Join failed: {r.status_code} {r.text}")
            return
        print(f"    ✓ Joined (or already joined)")

        # ── 5. Start session ─────────────────────────────────
        print("\n[5] Starting session...")
        r = await client.post(f"/api/sessions/{session_id}/start", headers=headers)
        if r.status_code != 200:
            print(f"    ⚠ Start: {r.status_code} {r.text[:100]}")
        else:
            print(f"    ✓ Session started")

        # ── 6. List units ────────────────────────────────────
        print("\n[6] Loading units...")
        r = await client.get(f"/api/sessions/{session_id}/units", headers=headers)
        units = r.json()
        blue_units = [u for u in units if u.get("side") == "blue"]
        print(f"    ✓ {len(units)} total units, {len(blue_units)} blue")
        for u in blue_units[:5]:
            print(f"      - {u['name']} ({u.get('unit_type', '?')}) id={u['id'][:8]}...")
        if len(blue_units) > 5:
            print(f"      ... and {len(blue_units) - 5} more")

        if not blue_units:
            print("    ✗ No blue units! Cannot test orders.")
            return

        first_unit = blue_units[0]
        first_unit_id = first_unit["id"]
        first_unit_name = first_unit["name"]

        # ── 7. Test: Submit a Russian COMMAND order ──────────
        print("\n" + "─" * 70)
        print("[7] TEST: Russian command order")
        print("─" * 70)
        test_text_ru = f"{first_unit_name}, срочно выдвигайтесь в квадрат B8 по улитке 2-4 с целью обнаружения противника!"
        print(f"    Text: \"{test_text_ru}\"")
        print(f"    Target: {first_unit_name} ({first_unit_id[:8]}...)")

        r = await client.post(f"/api/sessions/{session_id}/orders", headers=headers, json={
            "original_text": test_text_ru,
            "target_unit_ids": [first_unit_id],
        })
        print(f"    Response: {r.status_code}")
        order_data = r.json()
        print(f"    Order ID: {order_data.get('id', '?')[:8]}...")
        print(f"    Status: {order_data.get('status')}")
        print(f"    Processing: {order_data.get('processing', False)}")

        order_id_ru = order_data.get("id")

        # Wait for background pipeline to complete
        if order_data.get("processing"):
            print("    ⏳ Waiting for LLM pipeline (up to 30s)...")
            for i in range(30):
                await asyncio.sleep(1)
                r2 = await client.get(
                    f"/api/sessions/{session_id}/orders/{order_id_ru}",
                    headers=headers
                )
                od = r2.json()
                if od.get("status") != "pending":
                    print(f"    ✓ Pipeline complete! Status: {od['status']} ({i+1}s)")
                    break
                if i % 5 == 4:
                    print(f"      ... still pending ({i+1}s)")
            else:
                print("    ⚠ Pipeline timed out (may still be running)")

        # Fetch the final order state
        r = await client.get(f"/api/sessions/{session_id}/orders/{order_id_ru}", headers=headers)
        final_order = r.json()
        print(f"\n    Final order state:")
        print(f"      Status: {final_order.get('status')}")
        print(f"      Order type: {final_order.get('order_type')}")

        parsed = final_order.get("parsed_order", {})
        if parsed:
            print(f"      Classification: {parsed.get('classification', '?')}")
            print(f"      Language: {parsed.get('language', '?')}")
            print(f"      Confidence: {parsed.get('confidence', '?')}")
            target_refs = parsed.get("target_unit_refs", [])
            if target_refs:
                print(f"      Target unit refs: {target_refs}")
            loc_refs = parsed.get("location_refs", [])
            if loc_refs:
                print(f"      Location refs: {json.dumps(loc_refs, ensure_ascii=False, indent=8)}")

        intent = final_order.get("parsed_intent", {})
        if intent:
            print(f"      Intent action: {intent.get('action', '?')}")
            print(f"      Intent purpose: {intent.get('purpose', '?')}")
            print(f"      Intent priority: {intent.get('priority', '?')}")

        # ── 8. Test: Submit a STATUS REQUEST ─────────────────
        print("\n" + "─" * 70)
        print("[8] TEST: Russian status request")
        print("─" * 70)
        test_text_status = f"{first_unit_name}, доложите обстановку!"
        print(f"    Text: \"{test_text_status}\"")

        r = await client.post(f"/api/sessions/{session_id}/orders", headers=headers, json={
            "original_text": test_text_status,
            "target_unit_ids": [first_unit_id],
        })
        order_status_req = r.json()
        order_id_sr = order_status_req.get("id")
        print(f"    Order ID: {order_id_sr[:8]}... Status: {order_status_req.get('status')}")

        if order_status_req.get("processing"):
            print("    ⏳ Waiting...")
            for i in range(25):
                await asyncio.sleep(1)
                r2 = await client.get(
                    f"/api/sessions/{session_id}/orders/{order_id_sr}",
                    headers=headers
                )
                if r2.json().get("status") != "pending":
                    break
            final = r2.json()
            classification = (final.get("parsed_order") or {}).get("classification", "?")
            print(f"    ✓ Status: {final.get('status')}, Classification: {classification}")

        # ── 9. Test: Submit an English command ───────────────
        print("\n" + "─" * 70)
        print("[9] TEST: English command order")
        print("─" * 70)
        test_text_en = "2nd Platoon, move to grid B4, snail 3-7. Slow and careful, hold fire."
        print(f"    Text: \"{test_text_en}\"")

        r = await client.post(f"/api/sessions/{session_id}/orders", headers=headers, json={
            "original_text": test_text_en,
        })
        order_en = r.json()
        order_id_en = order_en.get("id")
        print(f"    Order ID: {order_id_en[:8]}... Status: {order_en.get('status')}")

        if order_en.get("processing"):
            print("    ⏳ Waiting...")
            for i in range(25):
                await asyncio.sleep(1)
                r2 = await client.get(
                    f"/api/sessions/{session_id}/orders/{order_id_en}",
                    headers=headers
                )
                if r2.json().get("status") != "pending":
                    break
            final = r2.json()
            parsed = final.get("parsed_order") or {}
            print(f"    ✓ Status: {final['status']}, Class: {parsed.get('classification')}, "
                  f"Lang: {parsed.get('language')}, Conf: {parsed.get('confidence')}")

        # ── 10. Test: Acknowledgment message ─────────────────
        print("\n" + "─" * 70)
        print("[10] TEST: Russian acknowledgment")
        print("─" * 70)
        test_ack = "Здесь первый взвод. Так-точно, выполняем. Начали движение."
        print(f"    Text: \"{test_ack}\"")

        r = await client.post(f"/api/sessions/{session_id}/orders", headers=headers, json={
            "original_text": test_ack,
        })
        order_ack = r.json()
        order_id_ack = order_ack.get("id")

        if order_ack.get("processing"):
            for i in range(10):
                await asyncio.sleep(1)
                r2 = await client.get(
                    f"/api/sessions/{session_id}/orders/{order_id_ack}",
                    headers=headers
                )
                if r2.json().get("status") != "pending":
                    break
            final = r2.json()
            parsed = final.get("parsed_order") or {}
            print(f"    ✓ Status: {final['status']}, Class: {parsed.get('classification')}")

        # ── 11. Test: Location resolve endpoint ──────────────
        print("\n" + "─" * 70)
        print("[11] TEST: Location resolve API")
        print("─" * 70)
        r = await client.post(
            f"/api/sessions/{session_id}/locations/resolve",
            headers=headers,
            json={"text": "Move to B8-2-4, then hold at C7. Coordinates 48.85,2.35."},
        )
        loc_result = r.json()
        refs = loc_result.get("references", [])
        print(f"    Resolved {len(refs)} location(s):")
        for ref in refs:
            print(f"      {ref.get('ref_type')}: {ref.get('normalized_ref')} "
                  f"→ lat={ref.get('lat')}, lon={ref.get('lon')} "
                  f"(conf={ref.get('confidence')})")

        # ── 12. List all orders for the session ──────────────
        print("\n" + "─" * 70)
        print("[12] All orders for this session:")
        print("─" * 70)
        r = await client.get(f"/api/sessions/{session_id}/orders", headers=headers)
        all_orders = r.json()
        for o in all_orders:
            cls = o.get("classification") or "?"
            lang = o.get("language") or "?"
            print(f"    [{o['status']:>10}] {cls:>15} ({lang}) "
                  f"| {(o.get('original_text') or '')[:60]}")

        # ── 13. Check chat messages (unit radio responses) ───
        print("\n" + "─" * 70)
        print("[13] Chat messages (unit radio responses):")
        print("─" * 70)
        r = await client.get(f"/api/sessions/{session_id}/chat", headers=headers)
        chats = r.json()
        unit_responses = [c for c in chats if c.get("sender_name", "").startswith("📻")]
        print(f"    Total chat messages: {len(chats)}, unit responses: {len(unit_responses)}")
        for c in unit_responses:
            text = c['text']
            # Highlight situational awareness content
            has_position = any(kw in text for kw in ["Позиция:", "Position:"])
            has_terrain = any(kw in text for kw in ["Местность:", "Terrain:"])
            has_contacts = any(kw in text for kw in ["Противник", "Contact", "enemy"])
            has_friendlies = any(kw in text for kw in ["Рядом свои:", "Friendlies nearby:"])
            badges = ""
            if has_position:
                badges += " 📍"
            if has_terrain:
                badges += " 🌍"
            if has_contacts:
                badges += " ⚠"
            if has_friendlies:
                badges += " 🤝"
            print(f"      {c['sender_name']}{badges}:")
            # Print full text (wrap long lines)
            for line in text.split(". "):
                if line.strip():
                    print(f"        {line.strip()}.")

        # ── 14. Test: Status request → verify situational awareness ─
        print("\n" + "─" * 70)
        print("[14] TEST: Status report with situational awareness")
        print("─" * 70)
        # Use another blue unit
        if len(blue_units) > 1:
            second_unit = blue_units[1]
            second_name = second_unit["name"]
            test_status2 = f"{second_name}, report status!"
            print(f"    Text: \"{test_status2}\"")

            r = await client.post(f"/api/sessions/{session_id}/orders", headers=headers, json={
                "original_text": test_status2,
                "target_unit_ids": [second_unit["id"]],
            })
            order_s2 = r.json()
            order_id_s2 = order_s2.get("id")

            if order_s2.get("processing"):
                for i in range(25):
                    await asyncio.sleep(1)
                    r2 = await client.get(
                        f"/api/sessions/{session_id}/orders/{order_id_s2}",
                        headers=headers
                    )
                    if r2.json().get("status") != "pending":
                        break

            # Check chat for the status report
            await asyncio.sleep(1)
            r = await client.get(f"/api/sessions/{session_id}/chat", headers=headers)
            chats = r.json()
            new_responses = [c for c in chats
                            if c.get("sender_name", "").startswith("📻")
                            and second_name in c.get("sender_name", "")]
            if new_responses:
                latest = new_responses[-1]
                text = latest["text"]
                print(f"    ✓ Response from {latest['sender_name']}:")
                for line in text.split(". "):
                    if line.strip():
                        print(f"      {line.strip()}.")

                # Check for situational awareness fields
                sa_fields = {
                    "grid_ref": any(kw in text for kw in ["Позиция:", "Position:"]),
                    "terrain": any(kw in text for kw in ["Местность:", "Terrain:"]),
                    "contacts": any(kw in text for kw in ["Противник", "Contact", "enemy"]),
                    "friendlies": any(kw in text for kw in ["Рядом свои:", "Friendlies nearby:"]),
                }
                print(f"\n    Situational awareness in report:")
                for field, present in sa_fields.items():
                    print(f"      {'✓' if present else '○'} {field}")
            else:
                print(f"    ⚠ No response received from {second_name}")

        # ── 15. Test: CONTACT REPORT (enemy spotted) ────────────
        print("\n" + "─" * 70)
        print("[15] TEST: Russian contact report (status_report)")
        print("─" * 70)
        test_contact = "Командир, обнаружены силы противника числом до взвода в районе C7-8-3. Быстро движутся на юго-восток. Как понял, приём!"
        print(f"    Text: \"{test_contact[:70]}...\"")

        r = await client.post(f"/api/sessions/{session_id}/orders", headers=headers, json={
            "original_text": test_contact,
        })
        order_contact = r.json()
        order_id_c = order_contact.get("id")
        if order_contact.get("processing"):
            for i in range(15):
                await asyncio.sleep(1)
                r2 = await client.get(
                    f"/api/sessions/{session_id}/orders/{order_id_c}",
                    headers=headers
                )
                if r2.json().get("status") != "pending":
                    break
            final = r2.json()
            parsed = final.get("parsed_order") or {}
            print(f"    ✓ Status: {final['status']}, Class: {parsed.get('classification')}")
            if parsed.get("report_text"):
                print(f"    Report: {parsed.get('report_text')}")

        # ── Summary ──────────────────────────────────────────
        r = await client.get(f"/api/sessions/{session_id}/orders", headers=headers)
        all_orders = r.json()
        r = await client.get(f"/api/sessions/{session_id}/chat", headers=headers)
        chats = r.json()
        unit_responses = [c for c in chats if c.get("sender_name", "").startswith("📻")]

        # ── Summary ──────────────────────────────────────────
        print("\n" + "=" * 70)
        print("  Summary")
        print("=" * 70)
        print(f"  Session: {session_id[:8]}...")
        print(f"  Orders submitted: {len(all_orders)}")
        print(f"  Unit radio responses: {len(unit_responses)}")
        has_llm = any(
            (o.get("confidence") or (o.get("parsed_order") or {}).get("confidence", 0)) > 0.5
            for o in all_orders
        )
        print(f"  LLM mode: {'✓ OpenAI GPT-4.1' if has_llm else '⚠ Keyword fallback (no API key or quota)'}")
        print()
        print("  ► Open http://localhost:8000 in browser to see results on the map")
        print("  ► Check the Radio tab in the bottom command panel for unit responses")
        print("  ► Check the Orders tab in the sidebar for order status + classification")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())



