"""Optional lakehouse and data-plane service checks."""

from __future__ import annotations

from typing import Any

from .doctor_support import _prescription, _result

# ── optional lakehouse / data-plane services (CONCEPT:AU-OS.deployment.lakehouse-doctor) ──
#
# Every check below follows the same shape: absent (endpoint unconfigured) -> "skip",
# never fatal on any profile -- none of these services is required by any documented
# deployment_profile, so "not required" is unconditionally true and the tiny-vs-else
# split _check_config/_check_engine_domains use for a REQUIRED capability does not
# apply here. Once an operator opts in by naming an endpoint, a static (non-live)
# check reports "ok" (declared) and a `live=True` doctor additionally proves
# reachability -- mirroring _check_graph_connections/_check_langfuse.
#
# Each attaches a `_prescription()`: the exact checked-in manifest path
# (services/<name>/k8s/manifests.yaml, verified live against the running cluster
# 2026-08-16), the real AgentConfig env-var keys this doctor itself reads (never
# invented), and the documented gotcha for that service.


def _endpoint_host_port(endpoint: str, default_port: int) -> tuple[str, int] | None:
    """Parse a bare ``host:port`` or full URL into ``(host, port)``; ``None`` if invalid."""
    from urllib.parse import urlsplit

    raw = endpoint.strip()
    if not raw:
        return None
    candidate = raw if "://" in raw else f"//{raw}"
    parsed = urlsplit(candidate)
    host = parsed.hostname
    if not host:
        return None
    try:
        port = parsed.port or default_port
    except ValueError:
        return None
    return host, port


def _probe_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    """Bounded, defensive TCP reachability probe -- never raises."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_http(url: str, timeout: float = 2.0) -> tuple[bool, int | None]:
    """Bounded, defensive HTTP reachability probe -- never raises.

    Any HTTP response (even a 4xx from an unauthenticated probe) proves the
    service itself is up and answering, so any status code counts as reachable.
    """
    import urllib.parse

    from agent_utilities.core.http_client import create_http_client

    # The URL comes from operator config, so restrict the scheme explicitly. A
    # `file://` or custom scheme in a config value must never be fetched.
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        return False, None

    # Goes through `core.http_client` rather than `urllib.request.urlopen`
    # (D-CIM-4 / the HTTP egress boundary): a raw `urlopen` here bypassed the
    # airgap guard, the private-address rules, and the standard headers that
    # every other outbound call in this package is subject to -- and a doctor
    # probe reaches operator-supplied hosts, which is exactly the traffic that
    # boundary exists to govern. `allow_loopback` is set because the common
    # case is probing a service on this host.
    #
    # Any HTTP response, including a 4xx from an unauthenticated probe, proves
    # the service is up and answering, so `raise_for_status` is deliberately
    # NOT used -- the status is the return value, not an error.
    try:
        with create_http_client(timeout=timeout, allow_loopback=True) as client:
            response = client.get(url)
        return True, response.status_code
    except Exception:  # noqa: BLE001 - doctor is a defensive boundary
        return False, None


def _check_kafka(live: bool = False, *, probe_tcp: Any = None) -> dict[str, Any]:
    """Kafka event-streaming broker (pre-existing, ``services/kafka``)."""
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
        servers = str(cfg.kafka_bootstrap_servers or "").strip()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "kafka", "error", f"kafka config unavailable ({type(exc).__name__})"
        )

    prescription = _prescription(
        manifest_path="services/kafka/k8s/manifests.yaml",
        config_keys={"KAFKA_BOOTSTRAP_SERVERS": "<kafka-host>:9092"},
        gotcha=(
            "single-broker KRaft (broker+controller combined in one process), "
            "pinned to node r710 -- not a multi-broker cluster"
        ),
        scaling={
            "supported": False,
            "reason": "hostPath-backed singleton with Recreate strategy; replica-count "
            "scaling would fork/corrupt the shared local log directory",
        },
    )
    if not servers:
        return _result(
            "kafka",
            "skip",
            "Kafka event streaming is not configured (KAFKA_BOOTSTRAP_SERVERS unset)",
            remediation=(
                "deploy `services/kafka` and set KAFKA_BOOTSTRAP_SERVERS if a durable "
                "event ledger is needed"
            ),
            prescription=prescription,
            data={"configured": False, "live_probed": live},
        )
    host_port = _endpoint_host_port(servers.split(",")[0].strip(), 9092)
    if host_port is None:
        return _result(
            "kafka",
            "fail",
            "KAFKA_BOOTSTRAP_SERVERS is set but not a valid host:port",
            remediation=(
                "set KAFKA_BOOTSTRAP_SERVERS to a valid host:port, e.g. "
                "<kafka-host>:9092"
            ),
            prescription=prescription,
            data={"configured": True, "live_probed": live},
        )
    if not live:
        return _result(
            "kafka",
            "ok",
            "Kafka bootstrap server is configured; live proof not requested",
            data={"configured": True, "live_probed": False},
        )
    reachable = (probe_tcp or _probe_tcp)(*host_port)
    if not reachable:
        return _result(
            "kafka",
            "fail",
            "Kafka bootstrap server is configured but unreachable",
            remediation=(
                "verify the kafka Deployment is Running and the Service/port are correct"
            ),
            prescription=prescription,
            data={"configured": True, "live_probed": True, "reachable": False},
        )
    return _result(
        "kafka",
        "ok",
        "Kafka bootstrap server is reachable",
        data={"configured": True, "live_probed": True, "reachable": True},
    )


def _check_fuseki(live: bool = False, *, probe_http: Any = None) -> dict[str, Any]:
    """Jena/Fuseki SPARQL endpoint (pre-existing, ``services/apache-jena``)."""
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
        endpoint = str(cfg.kg_fuseki_endpoint or "").strip()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "fuseki", "error", f"fuseki config unavailable ({type(exc).__name__})"
        )

    prescription = _prescription(
        manifest_path="services/apache-jena/k8s/manifests.yaml",
        config_keys={
            "KG_FUSEKI_ENDPOINT": "http://<fuseki-host>",
            "GRAPH_FUSEKI_DATASET": "agent_kg",
        },
        gotcha="Service port 80 forwards to Fuseki's own default container port 3030",
        scaling={
            "supported": False,
            "reason": "single-instance dataset store; not horizontally scalable",
        },
    )
    if not endpoint:
        return _result(
            "fuseki",
            "skip",
            "the Jena/Fuseki SPARQL endpoint is not configured (KG_FUSEKI_ENDPOINT unset)",
            remediation=(
                "deploy `services/apache-jena` and set KG_FUSEKI_ENDPOINT if ontology "
                "publish/query via Fuseki is needed"
            ),
            prescription=prescription,
            data={"configured": False, "live_probed": live},
        )
    if not live:
        return _result(
            "fuseki",
            "ok",
            "Fuseki endpoint is configured; live proof not requested",
            data={"configured": True, "live_probed": False},
        )
    ok, status = (probe_http or _probe_http)(endpoint.rstrip("/") + "/$/ping")
    data = {
        "configured": True,
        "live_probed": True,
        "reachable": ok,
        "http_status": status,
    }
    if not ok:
        return _result(
            "fuseki",
            "fail",
            "Fuseki endpoint is configured but unreachable",
            remediation=(
                "verify the fuseki Deployment is Running and KG_FUSEKI_ENDPOINT is correct"
            ),
            prescription=prescription,
            data=data,
        )
    return _result("fuseki", "ok", "Fuseki endpoint is reachable", data=data)


def _check_seaweedfs_s3(live: bool = False, *, probe_tcp: Any = None) -> dict[str, Any]:
    """SeaweedFS S3 gateway backing the Iceberg lakehouse (``services/lakehouse-seaweedfs``)."""
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
        endpoint = str(cfg.lakehouse_s3_endpoint or "").strip()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "seaweedfs_s3",
            "error",
            f"seaweedfs config unavailable ({type(exc).__name__})",
        )

    prescription = _prescription(
        manifest_path="services/lakehouse-seaweedfs/k8s/manifests.yaml",
        config_keys={"LAKEHOUSE_S3_ENDPOINT": "http://<s3-gateway-host>:8333"},
        gotcha=(
            "no S3 STS -- per-warehouse credential vending stays disabled "
            "(sts-enabled=false on the warehouse); S3 identity/credentials come from "
            "OpenBao apps/lakehouse-seaweedfs (S3_ACCESS_KEY/S3_SECRET_KEY), never "
            "hand-typed into a manifest"
        ),
        scaling={
            "supported": False,
            "reason": "hostPath-backed singleton (master+volume+filer+S3 in one "
            "process); not horizontally scalable",
        },
    )
    if not endpoint:
        return _result(
            "seaweedfs_s3",
            "skip",
            "the SeaweedFS S3 lakehouse gateway is not configured "
            "(LAKEHOUSE_S3_ENDPOINT unset)",
            remediation=(
                "deploy `services/lakehouse-seaweedfs` and set LAKEHOUSE_S3_ENDPOINT "
                "if an Iceberg lakehouse is needed"
            ),
            prescription=prescription,
            data={"configured": False, "live_probed": live},
        )
    host_port = _endpoint_host_port(endpoint, 8333)
    if host_port is None:
        return _result(
            "seaweedfs_s3",
            "fail",
            "LAKEHOUSE_S3_ENDPOINT is set but not a valid URL/host:port",
            remediation=(
                "set LAKEHOUSE_S3_ENDPOINT to a valid URL, e.g. "
                "http://<s3-gateway-host>:8333"
            ),
            prescription=prescription,
            data={"configured": True, "live_probed": live},
        )
    if not live:
        return _result(
            "seaweedfs_s3",
            "ok",
            "the SeaweedFS S3 gateway is configured; live proof not requested",
            data={"configured": True, "live_probed": False},
        )
    reachable = (probe_tcp or _probe_tcp)(*host_port)
    if not reachable:
        return _result(
            "seaweedfs_s3",
            "fail",
            "the SeaweedFS S3 gateway is configured but unreachable",
            remediation=(
                "verify the lakehouse-seaweedfs Deployment is Running and "
                "LAKEHOUSE_S3_ENDPOINT is correct"
            ),
            prescription=prescription,
            data={"configured": True, "live_probed": True, "reachable": False},
        )
    return _result(
        "seaweedfs_s3",
        "ok",
        "the SeaweedFS S3 gateway is reachable",
        data={"configured": True, "live_probed": True, "reachable": True},
    )


def _lakekeeper_prescription() -> dict[str, Any]:
    """The machine-readable Lakekeeper remediation, including the known gotcha."""
    return _prescription(
        manifest_path="services/lakekeeper/k8s/manifests.yaml",
        config_keys={
            "LAKEKEEPER_CATALOG_URI": "http://<catalog-host>:8181/catalog",
            "LAKEKEEPER_OAUTH2_SCOPE": "lakekeeper",
        },
        gotcha=(
            "catalog URI must end in `/catalog` (requests land on `/catalog/v1/...`), "
            "NOT bare `/v1`; and the Iceberg REST client's default OAuth2 scope "
            "(`catalog`) is not granted to the deployed `lakekeeper-service` Keycloak "
            "machine client (realm homelab, issuer "
            "https://<idp-host>/realms/<realm>) -- only `lakekeeper` is, so scope "
            "must be pinned explicitly or the OAuth2 token exchange 400s with "
            "invalid_scope"
        ),
        scaling={
            "supported": False,
            "reason": "hostPath-adjacent singleton (Recreate strategy, node-pinned); "
            "not horizontally scalable",
        },
    )


def _lakekeeper_gotcha_findings(uri: str, scope: str) -> list[str]:
    """Configuration mistakes that make the catalog unusable, in report order."""
    findings = []
    if not uri.rstrip("/").endswith("/catalog"):
        findings.append(
            "LAKEKEEPER_CATALOG_URI does not end in /catalog -- catalog calls will 404"
        )
    if scope and scope != "lakekeeper":
        findings.append(
            f"LAKEKEEPER_OAUTH2_SCOPE={scope!r} is not 'lakekeeper' -- OAuth2 token "
            "exchange will 400 with invalid_scope"
        )
    return findings


def _check_lakekeeper(live: bool = False, *, probe_http: Any = None) -> dict[str, Any]:
    """Lakekeeper Iceberg REST catalog (``services/lakekeeper``)."""
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
        uri = str(cfg.lakekeeper_catalog_uri or "").strip()
        scope = str(cfg.lakekeeper_oauth2_scope or "").strip()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "lakekeeper",
            "error",
            f"lakekeeper config unavailable ({type(exc).__name__})",
        )

    prescription = _lakekeeper_prescription()
    if not uri:
        return _result(
            "lakekeeper",
            "skip",
            "the Lakekeeper Iceberg REST catalog is not configured "
            "(LAKEKEEPER_CATALOG_URI unset)",
            remediation=(
                "deploy `services/lakekeeper` and set LAKEKEEPER_CATALOG_URI if an "
                "Iceberg catalog is needed"
            ),
            prescription=prescription,
            data={"configured": False, "live_probed": live},
        )
    findings = _lakekeeper_gotcha_findings(uri, scope)
    data: dict[str, Any] = {
        "configured": True,
        "live_probed": live,
        "gotcha_findings": findings,
    }
    if findings:
        return _result(
            "lakekeeper",
            "fail",
            "; ".join(findings),
            remediation=(
                "fix LAKEKEEPER_CATALOG_URI / LAKEKEEPER_OAUTH2_SCOPE per the known gotcha"
            ),
            prescription=prescription,
            data=data,
        )
    if not live:
        return _result(
            "lakekeeper",
            "ok",
            "Lakekeeper catalog is configured correctly; live proof not requested",
            data=data,
        )
    ok, status = (probe_http or _probe_http)(uri.rstrip("/") + "/v1/config")
    data["reachable"] = ok
    data["http_status"] = status
    if not ok:
        return _result(
            "lakekeeper",
            "fail",
            "Lakekeeper catalog is configured but unreachable",
            remediation=(
                "verify the lakekeeper Deployment is Running and LAKEKEEPER_CATALOG_URI "
                "is correct"
            ),
            prescription=prescription,
            data=data,
        )
    return _result("lakekeeper", "ok", "Lakekeeper catalog is reachable", data=data)


def _check_lakekeeper_db(live: bool = False) -> dict[str, Any]:
    """Dedicated Lakekeeper Postgres (``services/lakekeeper-db``).

    This doctor never resolves secret material or opens a database connection --
    a configured runtime secret ref is the strongest fail-closed proof this check
    can offer without handling credentials. When a live probe is requested but
    genuinely cannot be performed, this reports ``warn`` (not ``ok``): absence of
    proof is not proof of health.
    """
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
        ref = cfg.lakekeeper_db_uri_ref
    except Exception as exc:  # noqa: BLE001
        return _result(
            "lakekeeper_db",
            "error",
            f"lakekeeper_db config unavailable ({type(exc).__name__})",
        )

    prescription = _prescription(
        manifest_path="services/lakekeeper-db/k8s/manifests.yaml",
        config_keys={
            "LAKEKEEPER_DB_URI_REF": "vault://apps/lakekeeper-db#uri (a runtime secret "
            "reference, never a literal DSN)"
        },
        gotcha=(
            "dedicated Postgres 17 per the 'every app runs its own Postgres, no shared "
            "operator' convention; readiness is `pg_isready -U lakekeeper`"
        ),
        scaling={
            "supported": False,
            "reason": "hostPath-backed singleton Postgres; not horizontally scalable",
        },
    )
    if not ref:
        return _result(
            "lakekeeper_db",
            "skip",
            "the Lakekeeper Postgres DSN is not configured (LAKEKEEPER_DB_URI_REF unset)",
            remediation=(
                "deploy `services/lakekeeper-db` and set LAKEKEEPER_DB_URI_REF to a "
                "runtime secret reference if Lakekeeper is deployed"
            ),
            prescription=prescription,
            data={"configured": False, "live_probed": live},
        )
    if not live:
        return _result(
            "lakekeeper_db",
            "ok",
            "a Lakekeeper Postgres DSN reference is configured; live proof not requested",
            data={"configured": True, "live_probed": False},
        )
    return _result(
        "lakekeeper_db",
        "warn",
        "a Lakekeeper Postgres DSN reference is configured, but this doctor cannot "
        "safely open a database connection to prove reachability (it never resolves "
        "or handles database credentials)",
        remediation=(
            "verify reachability with `pg_isready` inside the pod, or check the "
            "lakekeeper-db Deployment's own readiness probe"
        ),
        prescription=prescription,
        data={"configured": True, "live_probed": True, "reachable": None},
    )


def _check_trino(live: bool = False, *, probe_http: Any = None) -> dict[str, Any]:
    """Trino coordinator (``services/trino``)."""
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
        endpoint = str(cfg.trino_endpoint or "").strip()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "trino", "error", f"trino config unavailable ({type(exc).__name__})"
        )

    prescription = _prescription(
        manifest_path="services/trino/k8s/manifests.yaml",
        config_keys={"TRINO_ENDPOINT": "http://<trino-host>:8080"},
        gotcha=(
            "image pinned to tag 476, NOT latest -- the pinned node's CPU predates the "
            "x86-64-v3 (AVX2) baseline trino images >=477 are compiled against; "
            "re-probe on that node before ever bumping the tag"
        ),
        scaling={
            "supported": False,
            "reason": "single-node coordinator, node-pinned (CPU baseline "
            "constraint); not horizontally scalable",
        },
    )
    if not endpoint:
        return _result(
            "trino",
            "skip",
            "Trino is not configured (TRINO_ENDPOINT unset)",
            remediation=(
                "deploy `services/trino` and set TRINO_ENDPOINT if federated SQL over "
                "the lakehouse is needed"
            ),
            prescription=prescription,
            data={"configured": False, "live_probed": live},
        )
    if not live:
        return _result(
            "trino",
            "ok",
            "Trino endpoint is configured; live proof not requested",
            data={"configured": True, "live_probed": False},
        )
    ok, status = (probe_http or _probe_http)(endpoint.rstrip("/") + "/v1/info")
    data = {
        "configured": True,
        "live_probed": True,
        "reachable": ok,
        "http_status": status,
    }
    if not ok:
        return _result(
            "trino",
            "fail",
            "Trino endpoint is configured but unreachable",
            remediation=(
                "verify the trino Deployment is Running (image tag 476) and "
                "TRINO_ENDPOINT is correct"
            ),
            prescription=prescription,
            data=data,
        )
    return _result("trino", "ok", "Trino endpoint is reachable", data=data)


def _check_spark_runner(live: bool = False, *, probe_tcp: Any = None) -> dict[str, Any]:
    """Spark exec-driven job runner (``services/spark``, Deployment ``spark-runner``)."""
    try:
        from agent_utilities.core.config import AgentConfig

        cfg = AgentConfig()
        endpoint = str(cfg.spark_runner_endpoint or "").strip()
    except Exception as exc:  # noqa: BLE001
        return _result(
            "spark_runner",
            "error",
            f"spark_runner config unavailable ({type(exc).__name__})",
        )

    prescription = _prescription(
        manifest_path="services/spark/k8s/manifests.yaml",
        config_keys={"SPARK_RUNNER_ENDPOINT": "http://<spark-runner-host>:4040"},
        gotcha=(
            "exec-driven: the pod runs `sleep infinity`, not a submission API -- jobs "
            "run via `kubectl exec deploy/spark-runner -- /opt/spark-scripts/"
            "spark-sql.sh`, and the wrapper sets spark.ui.enabled=false, so port 4040 "
            "has NO listener between jobs -- an unreachable probe here does not by "
            "itself mean the pod is unhealthy"
        ),
        scaling={
            "supported": False,
            "reason": "a single long-lived exec target, not a job-parallel cluster; "
            "not horizontally scalable",
        },
    )
    if not endpoint:
        return _result(
            "spark_runner",
            "skip",
            "the Spark job runner is not configured (SPARK_RUNNER_ENDPOINT unset)",
            remediation=(
                "deploy `services/spark` and set SPARK_RUNNER_ENDPOINT if cross-engine "
                "Iceberg interop via Spark is needed"
            ),
            prescription=prescription,
            data={"configured": False, "live_probed": live},
        )
    if not live:
        return _result(
            "spark_runner",
            "ok",
            "the Spark job runner is configured; live proof not requested",
            data={"configured": True, "live_probed": False},
        )
    host_port = _endpoint_host_port(endpoint, 4040)
    reachable = (
        (probe_tcp or _probe_tcp)(*host_port) if host_port is not None else False
    )
    if reachable:
        return _result(
            "spark_runner",
            "ok",
            "the Spark job runner's UI port is reachable",
            data={"configured": True, "live_probed": True, "reachable": True},
        )
    return _result(
        "spark_runner",
        "warn",
        "the Spark job runner's UI port did not respond -- expected when idle "
        "(exec-driven, spark.ui.enabled=false between jobs); this alone does not "
        "prove the pod is down",
        remediation="use `kubectl -n apps get pod -l app=spark-runner` to confirm the pod is Running",
        prescription=prescription,
        data={"configured": True, "live_probed": True, "reachable": False},
    )
