"""Relation derivation: joins the signals of different applications."""

from stc.indexer.relations import (
    AppRef,
    RelationDraft,
    SignalRow,
    derive_relations,
    service_token,
)

_ids = iter(range(1, 10_000))


def sig(
    app: int, kind: str, role: str, key: str, host: str | None = None, detail: str = ""
) -> SignalRow:
    n = next(_ids)
    return SignalRow(n, app, app * 100, kind, role, key, host, detail or key, n, n)


def derive(apps: list[AppRef], *signals: SignalRow) -> dict[tuple[int, int, str], RelationDraft]:
    return {(d.source_app_id, d.target_app_id, d.type): d for d in derive_relations(apps, signals)}


APPS = [AppRef(1, "web"), AppRef(2, "billing-service"), AppRef(3, "orders"), AppRef(4, "api")]


def test_service_tokens_ignore_common_suffixes() -> None:
    assert service_token("Billing_Service") == "billing"
    assert service_token("billing-svc") == "billing"
    assert service_token("billing-api-service") == "billing"
    assert service_token("api") == "api" and service_token("service") == "service"


def test_http_by_host_is_high_confidence() -> None:
    found = derive(
        APPS, sig(1, "http", "consumes", "/invoices/{}", "billing", "http://billing/invoices/1")
    )
    relation = found[(1, 2, "http")]
    assert relation.confidence == "high" and relation.detail == "http://billing/invoices/1"
    assert (relation.evidence_file_id, relation.evidence_count) == (100, 1)


def test_cluster_internal_hosts_count_but_public_hosts_do_not() -> None:
    found = derive(
        APPS,
        sig(
            1,
            "http",
            "consumes",
            "/x/y",
            "orders.prod.svc.cluster.local",
            "http://orders.prod.svc.cluster.local/x/y",
        ),
        sig(
            1,
            "http",
            "consumes",
            "/v1/charges",
            "api.stripe.com",
            "https://api.stripe.com/v1/charges",
        ),
        sig(
            3,
            "http",
            "consumes",
            "/v1/thing",
            "api.example.org",
            "https://api.example.org/v1/thing",
        ),
    )
    assert set(found) == {(1, 3, "http")}  # `api.stripe.com` must not match the app called "api"


def test_configured_host_aliases_resolve_public_names() -> None:
    apps = [AppRef(1, "web"), AppRef(2, "billing", hosts=("billing.mycompany.com",))]
    found = derive(
        apps,
        sig(
            1,
            "http",
            "consumes",
            "/inv",
            "billing.mycompany.com",
            "https://billing.mycompany.com/inv",
        ),
    )
    assert (1, 2, "http") in found


def test_env_var_hints_are_medium_confidence() -> None:
    found = derive(
        APPS, sig(1, "http", "consumes", "", "billing-service", "via BILLING_SERVICE_URL")
    )
    assert found[(1, 2, "http")].confidence == "medium"


def test_path_match_requires_a_unique_provider() -> None:
    provided = sig(2, "http", "provides", "/invoices/{}", None, "GET /invoices/{id}")
    call = sig(1, "http", "consumes", "/invoices/{}", None, "/invoices/1")
    assert derive(APPS, provided, call)[(1, 2, "http")].confidence == "medium"
    # a second application serving the same path makes the match ambiguous: no relation
    assert derive(APPS, provided, sig(3, "http", "provides", "/invoices/{}"), call) == {}


def test_path_suffix_match_is_low_and_generic_paths_never_match() -> None:
    provided = sig(2, "http", "provides", "/invoices/{}")
    suffix = sig(1, "http", "consumes", "/api/v1/invoices/{}")
    assert derive(APPS, provided, suffix)[(1, 2, "http")].confidence == "low"
    health = sig(1, "http", "consumes", "/health")
    assert derive(APPS, sig(2, "http", "provides", "/health"), health) == {}


def test_an_application_never_relates_to_itself() -> None:
    assert (
        derive(
            APPS, sig(1, "http", "provides", "/orders/{}"), sig(1, "http", "consumes", "/orders/{}")
        )
        == {}
    )
    assert derive(APPS, sig(2, "http", "consumes", "/x/y", "billing", "http://billing/x/y")) == {}


def test_messaging_goes_from_publisher_to_subscriber() -> None:
    found = derive(
        APPS,
        sig(3, "messaging", "publishes", "orders.created"),
        sig(2, "messaging", "subscribes", "orders.created"),
        sig(1, "messaging", "subscribes", "other.topic"),
    )
    assert set(found) == {(3, 2, "messaging")}
    assert found[(3, 2, "messaging")].detail == "topic orders.created"


def test_library_dependents_point_at_the_provider() -> None:
    found = derive(
        APPS,
        sig(2, "library", "provides", "acme-core"),
        sig(1, "library", "consumes", "acme-core"),
        sig(3, "library", "consumes", "react"),
    )
    assert set(found) == {(1, 2, "library")} and found[(1, 2, "library")].confidence == "high"


def test_shared_database_is_undirected_and_generic_hosts_are_low() -> None:
    found = derive(
        APPS,
        sig(3, "database", "uses", "postgresql://pg.corp:5432/ledger", "pg.corp"),
        sig(2, "database", "uses", "postgresql://pg.corp:5432/ledger", "pg.corp"),
        sig(1, "database", "uses", "postgresql://localhost:5432/app", "localhost"),
        sig(4, "database", "uses", "postgresql://localhost:5432/app", "localhost"),
    )
    assert set(found) == {(2, 3, "shared_database"), (1, 4, "shared_database")}  # lower id first
    assert found[(2, 3, "shared_database")].confidence == "medium"
    assert found[(1, 4, "shared_database")].confidence == "low"


def test_evidence_picks_the_strongest_signal_and_counts_all() -> None:
    weak = sig(1, "http", "consumes", "", "billing", "via BILLING_URL")
    strong = sig(1, "http", "consumes", "/a/b", "billing", "http://billing/a/b")
    relation = derive(APPS, weak, strong)[(1, 2, "http")]
    assert relation.confidence == "high" and relation.evidence_count == 2
    assert relation.evidence_start_line == strong.start_line
    assert "http://billing/a/b" in relation.detail and "via BILLING_URL" in relation.detail
