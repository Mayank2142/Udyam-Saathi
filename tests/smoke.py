"""End-to-end HTTP checks for the synthetic Docker stack. Uses only the owner-qa account."""
import argparse
import json
import time
from html.parser import HTMLParser

import httpx


class LoginForm(HTMLParser):
    action = ""

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "form" and attr.get("id") == "kc-form-login":
            self.action = attr["action"]


def sign_in(client, username, password):
    page = client.get("/api/auth/login", follow_redirects=True)
    form = LoginForm()
    form.feed(page.text)
    if not form.action.startswith("http://127.0.0.1:8175/realms/saathi/"):
        raise RuntimeError("The local Keycloak login page is unavailable")
    # HTTPX normally drops Secure cookies on HTTP loopback. Chromium keeps them.
    keycloak_cookies = "; ".join(
        f"{cookie.name}={cookie.value}" for cookie in client.cookies.jar
        if cookie.name in {"AUTH_SESSION_ID", "KC_AUTH_SESSION_HASH", "KC_RESTART"}
    )
    client.post(form.action, data={"username": username, "password": password, "credentialId": ""},
                headers={"Cookie": keycloak_cookies, "Origin": "http://127.0.0.1:8175", "Referer": str(page.url)},
                follow_redirects=True).raise_for_status()
    return client.get("/api/session").raise_for_status().json()


def post(client, route, data=None):
    return client.post(route, json=data).raise_for_status().json()


def demo_client(base, timeout):
    if base != "http://127.0.0.1:8174":
        raise ValueError("The synthetic login check only runs on the local sandbox origin")
    return httpx.Client(base_url=base, timeout=timeout, trust_env=False)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--base", default="http://127.0.0.1:8174")
    cli.add_argument("--skip-model", action="store_true")
    cli.add_argument("--quote-only", action="store_true")
    args = cli.parse_args()
    outcomes = {}
    with demo_client(args.base, 180) as owner, demo_client(args.base, 30) as partner:
        health = owner.get("/api/health").raise_for_status().json()
        assert health["storage"] == "postgres" and health["catalogue_products"] == 25
        outcomes["catalogue_and_storage"] = health
        role = sign_in(owner, "owner-qa", "saathi-owner-qa")
        assert role["role"] == "owner", role
        outcomes["oidc_owner"] = True
        case = owner.get("/api/case").raise_for_status().json()
        needs = {
            "segment": "office", "employees": 5, "sites": 1, "city_tier": 2, "speed": 300,
            "email_seats": 5, "devices": 5, "monthly_budget": 3500, "coverage": "fibre",
        }
        if not case or not case.get("accepted_at"):
            case = owner.put("/api/case", json={"revision": case["revision"] if case else 0, "needs": needs}).raise_for_status().json()
            assert case["quote"]["monthly_paise"] == 261900
            case = owner.put("/api/case", json={"revision": case["revision"], "needs": needs,
                                                "failover": True}).raise_for_status().json()
            assert case["quote"]["monthly_paise"] == 311800 and case["quote"]["setup_paise"] == 150000
            case = post(owner, "/api/case/accept", {"revision": case["revision"], "quote_id": case["quote"]["id"]})
        assert case["accepted_at"] and case["quote"]["monthly_paise"] == 311800
        outcomes["accepted_quote"] = {"monthly_inr": 3118, "setup_inr": 1500}
        if args.quote_only:
            print(json.dumps(outcomes, indent=2))
            return
        if not args.skip_model:
            examples = {
                "en": "An office with 5 employees, 1 site, 300 Mbps, email for 5 and protection for 5 devices.",
                "hi": "5 कर्मचारियों का कार्यालय, 1 स्थान, 300 Mbps और 5 ईमेल उपयोगकर्ता।",
                "mr": "5 कर्मचाऱ्यांचे कार्यालय, 1 ठिकाण, 300 Mbps आणि 5 ईमेल वापरकर्ते.",
            }
            outcomes["advisor"] = {}
            for language, example in examples.items():
                parsed = post(owner, "/api/advisor", {"text": example, "language": language})
                assert parsed["fields"].get("employees") == 5, parsed
                assert parsed["fields"].get("speed") == 300 and "monthly_budget" not in parsed["fields"], parsed
                outcomes["advisor"][language] = parsed
        flow = owner.get("/api/activation").raise_for_status().json()
        if not flow["order"]:
            consent = {"accepted": True, "notice": "saathi-synthetic-v1", "language": "en",
                       "business": "Trinity Bake House Pvt Ltd"}
            if not flow["consented"]:
                assert owner.post("/api/activation/specimens/K11/address").status_code == 409
            post(owner, "/api/activation/consent", consent)
            result = post(owner, "/api/activation/specimens/K11/address")
            assert result["docs"]["address"].get("age_days") == 257, result["docs"]["address"]
            assert result["docs"]["address"]["reason"] == "stale_proof"
            assert owner.post("/api/activation/order").status_code == 409
            outcomes["stale_address_proof"] = result["docs"]["address"]
            result = post(owner, "/api/activation/handoff")
            assert result["handoff"]["status"] in {"queued", "reviewed"}
            assert owner.get("/api/activation/partner").status_code == 401
            assert sign_in(partner, "partner-demo", "saathi-partner-demo")["role"] == "partner"
            entries = partner.get("/api/activation/partner").raise_for_status().json()
            assignment = next(item for item in entries if item["id"] == result["handoff"]["id"])
            assert "stale_proof" in assignment["blockers"]
            post(partner, f"/api/activation/partner/{assignment['id']}/acknowledge")
            assert owner.get("/api/activation").raise_for_status().json()["handoff"]["status"] == "reviewed"
            outcomes["partner_handoff"] = True
            replacement = post(owner, "/api/activation/replacement")
            assert replacement["docs"]["address"]["status"] == "pass", replacement["docs"]["address"]
            outcomes["corrected_address_proof"] = replacement["docs"]["address"]
            for kind in ("signatory", "gst", "udyam"):
                evidence = post(owner, f"/api/activation/specimens/K11/{kind}")
                assert evidence["docs"][kind]["status"] == "pass", evidence["docs"][kind]
                outcomes[kind] = evidence["docs"][kind]
            flow = post(owner, "/api/activation/order")
        assert post(owner, "/api/activation/order")["order"]["id"] == flow["order"]["id"]
        for _ in range(22):
            status = owner.get("/api/activation").raise_for_status().json()["order"]
            if status["status"] == "active":
                break
            time.sleep(2)
        assert status["status"] == "active", status
        assert len(status["events"]) == 5 and all(line["state"] == "active" for line in status["lines"])
        outcomes["order_tracker"] = [event["step"] for event in status["events"]]
    print(json.dumps(outcomes, indent=2))


if __name__ == "__main__":
    main()
