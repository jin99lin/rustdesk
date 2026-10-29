//! Adoluna: account-based passwordless access ("免密码登录") and server-managed device role.
//!
//! * Ticket: the API server signs, per connection,
//!   `"adoluna-tkt-v1\n{peer_id}\n{my_id}\n{user}\n{challenge}\n{exp}"` with a dedicated ed25519 key.
//!   The controlled side accepts it instead of a password only when: device role allows account
//!   login, not controller-only, signature valid, peer_id == own id, my_id == LoginRequest.my_id,
//!   challenge == this connection's challenge, exp in (now, now+300], and the ticket was not used before.
//! * Role: heartbeat may carry `adoluna_cfg` = {a, c, t, sig}, signature over
//!   `"adoluna-cfg-v1\n{id}\n{a}\n{c}\n{t}"`; applied only if `t` is newer than the stored one.
//! Anything that does not verify falls back to the normal password flow.
use hbb_common::{
    anyhow::anyhow,
    base64::{engine::general_purpose::STANDARD, Engine},
    config::Config,
    log,
    sodiumoxide::crypto::sign,
    ResultType,
};
use serde_derive::Deserialize;
use std::{collections::VecDeque, sync::Mutex};

pub const TICKET_PUBKEY: &str = "Zdvtyy1kLQkRp0JMKgvgG9NG/6mjWEzgm3Ttrzk4pTw=";
pub const OPT_ACCEPT: &str = "adoluna-accept-account-login";
pub const OPT_CTRL_ONLY: &str = "adoluna-controller-only";
const OPT_CFG_TS: &str = "adoluna-cfg-ts";
const MAX_TTL: i64 = 300;
pub const LOGIN_MSG_CONTROLLER_ONLY: &str = "This device is controller-only and does not accept connections";

lazy_static::lazy_static! {
    static ref USED: Mutex<VecDeque<String>> = Mutex::new(VecDeque::new());
}

#[derive(Debug, Deserialize)]
struct Ticket {
    u: String,
    e: i64,
    sig: String,
}

#[derive(Debug, Deserialize)]
struct Cfg {
    a: u8,
    c: u8,
    t: i64,
    sig: String,
}

fn pk() -> ResultType<sign::PublicKey> {
    sign::PublicKey::from_slice(&STANDARD.decode(TICKET_PUBKEY)?).ok_or(anyhow!("bad built-in key"))
}

fn verify_sig(pk: &sign::PublicKey, msg: &str, sig_b64: &str) -> ResultType<()> {
    let sig = STANDARD.decode(sig_b64.as_bytes())?;
    let sig = sign::Signature::from_bytes(&sig).map_err(|_| anyhow!("bad signature format"))?;
    if !sign::verify_detached(&sig, msg.as_bytes(), pk) {
        return Err(anyhow!("signature check failed"));
    }
    Ok(())
}

pub fn ticket_msg(peer_id: &str, my_id: &str, user: &str, challenge: &str, exp: i64) -> String {
    format!("adoluna-tkt-v1\n{peer_id}\n{my_id}\n{user}\n{challenge}\n{exp}")
}

/// Pure verification (no device-role / replay checks). Returns the account name.
pub fn verify_ticket_with(
    pk: &sign::PublicKey,
    ticket: &str,
    peer_id: &str,
    my_id: &str,
    challenge: &str,
    now: i64,
) -> ResultType<String> {
    if ticket.is_empty() || peer_id.is_empty() || my_id.is_empty() || challenge.is_empty() {
        return Err(anyhow!("empty field"));
    }
    let t: Ticket = serde_json::from_str(ticket)?;
    if t.u.is_empty() || t.u.contains('\n') || t.u.len() > 128 {
        return Err(anyhow!("bad user"));
    }
    if t.e <= now || t.e > now + MAX_TTL {
        return Err(anyhow!("expired or too long"));
    }
    verify_sig(pk, &ticket_msg(peer_id, my_id, &t.u, challenge, t.e), &t.sig)?;
    Ok(t.u)
}

fn mark_used(sig: &str) -> bool {
    let mut u = USED.lock().unwrap();
    if u.iter().any(|x| x == sig) {
        return false;
    }
    u.push_back(sig.to_owned());
    while u.len() > 256 {
        u.pop_front();
    }
    true
}

pub fn accept_enabled() -> bool {
    Config::get_option(OPT_ACCEPT) == "Y" && !controller_only()
}

pub fn controller_only() -> bool {
    Config::get_option(OPT_CTRL_ONLY) == "Y"
}

/// Full check used by the controlled side. Returns Some(account) only when passwordless
/// access must be granted; None means "continue with the normal password flow".
pub fn check_login_ticket(ticket: &str, my_id: &str, challenge: &str) -> Option<String> {
    if ticket.is_empty() {
        return None;
    }
    if !accept_enabled() {
        log::info!("Adoluna ticket ignored: account login not enabled on this device");
        return None;
    }
    let now = hbb_common::get_time() / 1000;
    let pk = pk().ok()?;
    match verify_ticket_with(&pk, ticket, &Config::get_id(), my_id, challenge, now) {
        Ok(user) => {
            let sig = serde_json::from_str::<Ticket>(ticket).map(|t| t.sig).unwrap_or_default();
            if !mark_used(&sig) {
                log::warn!("Adoluna ticket replay rejected");
                return None;
            }
            Some(user)
        }
        Err(e) => {
            log::warn!("Adoluna ticket rejected: {e}");
            None
        }
    }
}

pub fn cfg_msg(id: &str, a: u8, c: u8, t: i64) -> String {
    format!("adoluna-cfg-v1\n{id}\n{a}\n{c}\n{t}")
}

/// Verify a role config; returns (accept, controller_only, ts) when valid and newer than `last_ts`.
pub fn verify_cfg_with(
    pk: &sign::PublicKey,
    v: &serde_json::Value,
    id: &str,
    last_ts: i64,
) -> ResultType<Option<(bool, bool, i64)>> {
    let c: Cfg = serde_json::from_value(v.clone())?;
    if c.t <= last_ts {
        return Ok(None);
    }
    if c.a > 1 || c.c > 1 {
        return Err(anyhow!("bad cfg"));
    }
    verify_sig(pk, &cfg_msg(id, c.a, c.c, c.t), &c.sig)?;
    Ok(Some((c.a == 1, c.c == 1, c.t)))
}

pub fn cfg_ts() -> i64 {
    Config::get_option(OPT_CFG_TS).parse().unwrap_or(0)
}

pub fn apply_cfg(v: serde_json::Value) {
    let res = pk().and_then(|pk| verify_cfg_with(&pk, &v, &Config::get_id(), cfg_ts()));
    match res {
        Ok(Some((a, c, t))) => {
            Config::set_option(OPT_ACCEPT.to_owned(), if a { "Y" } else { "" }.to_owned());
            Config::set_option(OPT_CTRL_ONLY.to_owned(), if c { "Y" } else { "" }.to_owned());
            Config::set_option(OPT_CFG_TS.to_owned(), t.to_string());
            log::info!("Adoluna device role updated: account_login={a} controller_only={c}");
        }
        Ok(None) => {}
        Err(e) => log::warn!("Adoluna device role rejected: {e}"),
    }
}

/// Controller side: ask the API server for a one-time ticket. Empty string on any failure.
pub async fn fetch_ticket(peer_id: &str, my_id: &str, challenge: &str) -> String {
    let token = hbb_common::config::LocalConfig::get_option("access_token");
    if token.is_empty() || challenge.is_empty() {
        return String::new();
    }
    let api = crate::ui_interface::get_api_server();
    if api.is_empty() || crate::is_public(&api) {
        return String::new();
    }
    let body = serde_json::json!({"peer_id": peer_id, "my_id": my_id, "challenge": challenge}).to_string();
    let header = format!("Authorization: Bearer {token}");
    let fut = crate::post_request(format!("{api}/api/adoluna/ticket"), body, &header);
    match hbb_common::tokio::time::timeout(std::time::Duration::from_secs(3), fut).await {
        Ok(Ok(s)) => match serde_json::from_str::<serde_json::Value>(&s) {
            Ok(v) if v.get("ticket").and_then(|t| t.as_str()).is_some() => {
                v["ticket"].as_str().unwrap_or_default().to_owned()
            }
            Ok(v) => {
                log::info!("Adoluna ticket not issued: {}", v.get("error").and_then(|e| e.as_str()).unwrap_or("?"));
                String::new()
            }
            Err(_) => String::new(),
        },
        _ => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn kp() -> (sign::PublicKey, sign::SecretKey) {
        sign::gen_keypair()
    }
    fn mk(sk: &sign::SecretKey, peer: &str, my: &str, u: &str, ch: &str, e: i64) -> String {
        let sig = sign::sign_detached(ticket_msg(peer, my, u, ch, e).as_bytes(), sk);
        serde_json::json!({"u": u, "e": e, "sig": STANDARD.encode(sig.to_bytes())}).to_string()
    }
    const NOW: i64 = 1_800_000_000;

    #[test]
    fn valid_ticket() {
        let (pk, sk) = kp();
        let t = mk(&sk, "1418613583", "22540916", "jay", "abc123", NOW + 60);
        assert_eq!(verify_ticket_with(&pk, &t, "1418613583", "22540916", "abc123", NOW).unwrap(), "jay");
    }
    #[test]
    fn empty_ticket_or_fields_never_pass() {
        let (pk, sk) = kp();
        assert!(verify_ticket_with(&pk, "", "1", "2", "c", NOW).is_err());
        let t = mk(&sk, "", "2", "u", "c", NOW + 60);
        assert!(verify_ticket_with(&pk, &t, "", "2", "c", NOW).is_err());
        let t = mk(&sk, "1", "2", "u", "", NOW + 60);
        assert!(verify_ticket_with(&pk, &t, "1", "2", "", NOW).is_err());
        assert!(verify_ticket_with(&pk, "{}", "1", "2", "c", NOW).is_err());
        assert!(verify_ticket_with(&pk, r#"{"u":"x","e":1800000060,"sig":""}"#, "1", "2", "c", NOW).is_err());
    }
    #[test]
    fn wrong_bindings_rejected() {
        let (pk, sk) = kp();
        let t = mk(&sk, "1418613583", "22540916", "jay", "abc123", NOW + 60);
        assert!(verify_ticket_with(&pk, &t, "291286511", "22540916", "abc123", NOW).is_err()); // other device
        assert!(verify_ticket_with(&pk, &t, "1418613583", "999", "abc123", NOW).is_err()); // other controller
        assert!(verify_ticket_with(&pk, &t, "1418613583", "22540916", "zzz999", NOW).is_err()); // other session
    }
    #[test]
    fn expiry_and_ttl() {
        let (pk, sk) = kp();
        let t = mk(&sk, "1", "2", "u", "c", NOW);
        assert!(verify_ticket_with(&pk, &t, "1", "2", "c", NOW).is_err());
        let t = mk(&sk, "1", "2", "u", "c", NOW + 3600);
        assert!(verify_ticket_with(&pk, &t, "1", "2", "c", NOW).is_err());
    }
    #[test]
    fn tampered_or_foreign_key() {
        let (pk, sk) = kp();
        let (_, sk2) = kp();
        let t = mk(&sk2, "1", "2", "u", "c", NOW + 60);
        assert!(verify_ticket_with(&pk, &t, "1", "2", "c", NOW).is_err());
        let t = mk(&sk, "1", "2", "u", "c", NOW + 60).replace("\"u\":\"u\"", "\"u\":\"admin\"");
        assert!(verify_ticket_with(&pk, &t, "1", "2", "c", NOW).is_err());
    }
    #[test]
    fn replay_set() {
        assert!(mark_used("sig-a"));
        assert!(!mark_used("sig-a"));
    }
    #[test]
    fn builtin_key_parses() {
        assert!(pk().is_ok());
    }
    #[test]
    fn cfg_verify() {
        let (pk, sk) = kp();
        let s = |a, c, t| {
            let sig = sign::sign_detached(cfg_msg("1418613583", a, c, t).as_bytes(), &sk);
            serde_json::json!({"a": a, "c": c, "t": t, "sig": STANDARD.encode(sig.to_bytes())})
        };
        assert_eq!(verify_cfg_with(&pk, &s(1, 0, 5), "1418613583", 0).unwrap(), Some((true, false, 5)));
        assert_eq!(verify_cfg_with(&pk, &s(1, 0, 5), "1418613583", 5).unwrap(), None);
        assert!(verify_cfg_with(&pk, &s(1, 0, 6), "291286511", 0).is_err());
        let mut v = s(0, 0, 7);
        v["a"] = serde_json::json!(1);
        assert!(verify_cfg_with(&pk, &v, "1418613583", 0).is_err());
    }
}
