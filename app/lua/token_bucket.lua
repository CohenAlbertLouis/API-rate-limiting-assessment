-- Token bucket (/foo). Mirrors token_bucket() in app/algorithms.py.
-- Runs atomically inside Redis: no other command can interleave.
-- KEYS[1]: bucket key
-- ARGV: capacity, refill_per_second, now (seconds; '' = use the Redis clock)
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
if not now then
  local t = redis.call('TIME')
  now = tonumber(t[1]) + tonumber(t[2]) / 1e6
end
local EPSILON = 1e-9

local saved = redis.call('HMGET', KEYS[1], 'tokens', 'updated_at')
local tokens, updated_at = tonumber(saved[1]), tonumber(saved[2])
if not tokens then
  tokens, updated_at = capacity, now
end

now = math.max(now, updated_at)
tokens = math.min(capacity, tokens + (now - updated_at) * rate)

local allowed, retry_after = 0, 0
if tokens >= 1 - EPSILON then
  allowed = 1
  tokens = math.max(0, tokens - 1)
else
  retry_after = (1 - tokens) / rate
end

local function fmt(x) return string.format('%.17g', x) end
redis.call('HSET', KEYS[1], 'tokens', fmt(tokens), 'updated_at', fmt(now))
-- forget the key 1 s after the bucket would be full again
redis.call('PEXPIRE', KEYS[1], math.ceil(((capacity - tokens) / rate + 1) * 1000))
return {allowed, '0', fmt(retry_after)}
