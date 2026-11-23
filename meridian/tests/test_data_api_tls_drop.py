"""How a broken TLS session surfaces through botocore's rds-data client, against a local server.

``never_sent`` decides which failures are sent again inside a transaction and for a commit, so
it must be true only when the request never arrived. A local TLS server ends the session at
each point and records whether the whole request reached it.
"""
import _socket
import datetime
import os
import socket
import ssl
import threading
from contextlib import contextmanager

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ConnectionClosedError, SSLError
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from backend.db import rds_data_client
from backend.db.rds_data_client import RDSDataClient, never_sent

ANSWER = (b'{"columnMetadata": [{"name": "one"}], "numberOfRecordsUpdated": 0,'
          b' "records": [[{"longValue": 1}]]}')


@pytest.fixture(autouse=True)
def loopback_only(isolated_unit_environment, monkeypatch):
    """Let the client reach this module's server on loopback; every other address stays refused."""
    guarded = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] in ("127.0.0.1", "::1"):
            return _socket.socket.connect(sock, address)
        return guarded(sock, address)
    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    """A throwaway self-signed certificate for localhost; nothing is kept after the run."""
    folder = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    return str(cert_path), str(key_path)


def _read_request(tls: ssl.SSLSocket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        data += tls.recv(65536)
    head, body = data.split(b"\r\n\r\n", 1)
    length = next(int(line.split(b":")[1]) for line in head.split(b"\r\n")
                  if line.lower().startswith(b"content-length"))
    while len(body) < length:
        body += tls.recv(65536)
    return body


def _serve_one(listener, context, mode, arrived):
    conn, _ = listener.accept()
    if mode == "drop_before_handshake":
        conn.close()
        return
    tls = context.wrap_socket(conn, server_side=True)
    arrived.append(_read_request(tls))
    if mode == "drop_after_request":
        tls.shutdown(socket.SHUT_RDWR)  # no TLS close_notify: the connection just ends
    elif mode == "broken_record_after_request":
        os.write(tls.fileno(), b"\x17\x03\x03\x00\x20" + os.urandom(32))
        tls.recv(1)  # wait for the client to give up
    else:
        tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: %d\r\n\r\n%s" % (len(ANSWER), ANSWER))
    tls.close()


@contextmanager
def tls_server(certificate, *modes):
    """Serve one connection per mode, in order; yield the endpoint and the requests that arrived."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*certificate)
    listener = socket.create_server(("127.0.0.1", 0))
    arrived: list = []

    def serve():
        for mode in modes:
            try:
                _serve_one(listener, context, mode, arrived)
            except OSError:
                pass  # the client hung up first; what arrived is already recorded
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield f"https://localhost:{listener.getsockname()[1]}", arrived
    finally:
        listener.close()
        worker.join(timeout=5)


def boto_client(endpoint, certificate):
    return boto3.client(
        "rds-data", region_name="us-east-1", endpoint_url=endpoint, verify=certificate[0],
        aws_access_key_id="test", aws_secret_access_key="test",
        config=Config(retries={"total_max_attempts": 1, "mode": "standard"},
                      connect_timeout=3, read_timeout=3),
    )


def execute(client):
    return client.execute_statement(
        resourceArn="arn:aws:rds:us-east-1:000000000000:cluster:test",
        secretArn="arn:aws:secretsmanager:us-east-1:000000000000:secret:test",
        sql="SELECT 1", transactionId="tx-1",
    )


def test_a_tls_session_dropped_before_the_handshake_was_never_sent(certificate):
    with tls_server(certificate, "drop_before_handshake") as (endpoint, arrived):
        with pytest.raises(SSLError) as raised:
            execute(boto_client(endpoint, certificate))
    assert arrived == []
    assert never_sent(raised.value)


def test_a_connection_dropped_after_the_request_arrived_may_have_run(certificate):
    with tls_server(certificate, "drop_after_request") as (endpoint, arrived):
        with pytest.raises(ConnectionClosedError) as raised:
            execute(boto_client(endpoint, certificate))
    assert len(arrived) == 1
    assert not never_sent(raised.value)


def test_a_broken_tls_record_after_the_request_arrived_may_have_run(certificate):
    """The case an SSLError alone cannot tell apart from a dropped handshake."""
    with tls_server(certificate, "broken_record_after_request") as (endpoint, arrived):
        with pytest.raises(SSLError) as raised:
            execute(boto_client(endpoint, certificate))
    assert len(arrived) == 1
    assert not never_sent(raised.value)


async def test_a_statement_in_a_transaction_is_sent_again_after_a_dropped_handshake(
    certificate, monkeypatch,
):
    monkeypatch.setattr(rds_data_client.time, "sleep", lambda _seconds: None)
    with tls_server(certificate, "drop_before_handshake", "answer") as (endpoint, arrived):
        client = RDSDataClient.__new__(RDSDataClient)
        client.cluster_arn = "arn:aws:rds:us-east-1:000000000000:cluster:test"
        client.secret_arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:test"
        client.database = "meridian"
        client.client = boto_client(endpoint, certificate)
        rows = await client.execute("SELECT 1 AS one", transaction_id="tx-1")
    assert rows == [{"one": 1}]
    assert len(arrived) == 1
