-- Leaky bucket with a queue (/bar). Mirrors leaky_bucket() in app/algorithms.py.
-- Runs atomically inside Redis: no other command can interleave.
-- KEYS[1]: bucket key (holds next_free, the time the next request may leave)
-- ARGV: drain_per_second, queue_depth, now (seconds; '' = use the Redis clock)
local interval = 1 / tonumber(ARGV[1])
local max_wait = tonumber(ARGV[2]) * interval
local now = tonumber(ARGV[3])
if not now then
  local t = redis.call('TIME')
  now = tonumber(t[1]) + tonumber(t[2]) / 1e6
end
local EPSILON = 1e-9
local function fmt(x) return string.format('%.17g', x) end

local next_free = tonumber(redis.call('GET', KEYS[1]))
local slot = now
if next_free then
  slot = math.max(now, next_free)
end
local wait = slot - now

if wait <= max_wait + EPSILON then
  local new_next_free = slot + interval
  -- forget the key 1 s after the bucket has fully drained
  redis.call('SET', KEYS[1], fmt(new_next_free), 'PX', math.ceil((new_next_free - now + 1) * 1000))
  return {1, fmt(wait), '0'}
end
return {0, '0', fmt(wait - max_wait)}
