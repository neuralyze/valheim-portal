"""Each world's FastLink list names that world, on a port nobody guessed.

The two ways this file has been wrong in production are the two properties here. It once
held one entry for the wrong world, so `test_a_world_gets_only_itself` pins the shape.
And the port is the trap: SERVER_PORT is 2456 inside every world's container, so reading
it hands every world the same port - `test_the_port_is_the_host_half` pins that the host
half of CONTAINER_VALHEIM_PORT is what a client joins.
"""

import pytest

import fastlink_servers as fastlink

ULFSLAND_ENV = """\
WORLD_NAME='Ulfsland'
SERVER_NAME='Ulfsland'
SERVER_PASS='DeepNorthTest26'
SERVER_PORT='2456'
CONTAINER_VALHEIM_PORT='2469-2470'
"""


@pytest.fixture
def stopped(monkeypatch):
    """No world is running, so every port comes from its env file."""
    monkeypatch.setattr(fastlink, "mapped_port", lambda world: None)


def test_the_port_is_the_host_half(stopped, tmp_path):
    env = tmp_path / "valheim.env"
    env.write_text(ULFSLAND_ENV)

    server = fastlink.describe("Ulfsland", "valheim.neuralyze.com:2469", env)

    assert server.port == 2469
    assert server.address == "valheim.neuralyze.com"
    assert server.password == "DeepNorthTest26"


def test_a_world_gets_only_itself(stopped, tmp_path):
    env = tmp_path / "valheim.env"
    env.write_text(ULFSLAND_ENV)

    rendered = fastlink.render(
        fastlink.describe("Ulfsland", "valheim.neuralyze.com:2469", env))

    entries = [line for line in rendered.splitlines()
               if line and not line.startswith(("#", " "))]
    assert entries == ["Ulfsland:"]
    assert "  port: 2469\n" in rendered


def test_a_port_the_portal_disagrees_with_is_refused(stopped, tmp_path):
    """Publishing either half of a disagreement silently is how this went wrong once."""
    env = tmp_path / "valheim.env"
    env.write_text(ULFSLAND_ENV)

    with pytest.raises(fastlink.GeneratorError) as refusal:
        fastlink.describe("Ulfsland", "valheim.neuralyze.com:2457", env)

    assert "2469" in str(refusal.value) and "2457" in str(refusal.value)


def test_a_running_world_is_measured_not_read(monkeypatch, tmp_path):
    """docker wins over the env file, and a stale env file is refused rather than used."""
    env = tmp_path / "valheim.env"
    env.write_text(ULFSLAND_ENV.replace("2469-2470", "2477-2478"))
    monkeypatch.setattr(fastlink, "mapped_port", lambda world: 2469)

    with pytest.raises(fastlink.GeneratorError) as refusal:
        fastlink.describe("Ulfsland", "valheim.neuralyze.com:2469", env)

    assert "2477" in str(refusal.value)


def test_a_world_with_no_password_ships_no_password_key(stopped, tmp_path):
    env = tmp_path / "valheim.env"
    env.write_text("CONTAINER_VALHEIM_PORT='2469-2470'\n")

    rendered = fastlink.render(
        fastlink.describe("Ulfsland", "valheim.neuralyze.com:2469", env))

    keys = [line for line in rendered.splitlines() if line.startswith("  ")]
    assert keys == ["  address: valheim.neuralyze.com", "  port: 2469"]


def test_a_password_that_would_start_a_comment_is_quoted(stopped, tmp_path):
    """FastLink's own template leaves passwords plain, which only works for plain ones."""
    env = tmp_path / "valheim.env"
    env.write_text("SERVER_PASS='#hash it'\nCONTAINER_VALHEIM_PORT='2469-2470'\n")

    rendered = fastlink.render(
        fastlink.describe("Ulfsland", "valheim.neuralyze.com:2469", env))

    assert "  password: '#hash it'\n" in rendered
