import pytest

from tests.mock_server import MockServer

RAW = b"Message-ID: <test@example>\r\nContent-Type: text/plain\r\n\r\nHello\r\n"


def test_authentication_and_mailboxes():
    server = MockServer()
    with pytest.raises(OSError):
        server.client(password="wrong")
    client = server.client()
    with pytest.raises(OSError):
        client.select("Missing")
    assert client.select("Missing", create=True) == 10
    assert client.inventory() == {}


@pytest.mark.parametrize("uidplus", [True, False])
def test_uids_fetch_search_delete_and_clients(uidplus):
    server = MockServer(uidplus=uidplus)
    a, b = server.client(), server.client()
    assert a.append(RAW) == (1 if uidplus else None)
    assert b.inventory() == {1: RAW}
    assert b.find_message("<test@example>") == [1]
    a.delete(1)
    assert b.inventory() == {}
    assert (1 in server.boxes["Notes"]) is (not uidplus)
    assert a.append(RAW) == (2 if uidplus else None)
    server.reset()
    assert a.select("Notes") == 11
    assert list(a.inventory()) == [3]
    a.close()
    assert server.history[-1] == "logout"


def test_failures_lost_response_and_scheduled_edits():
    server = MockServer()
    client = server.client()
    server.failures["append"] = "disconnected"
    with pytest.raises(OSError):
        client.append(RAW)
    assert not client.inventory()
    server.failures["append_response"] = "response lost"
    with pytest.raises(OSError):
        client.append(RAW)
    assert client.find_message("<test@example>") == [1]
    server.schedule("inventory", server.counts["inventory"] + 1, lambda: server.add(RAW))
    assert len(client.inventory()) == 2
