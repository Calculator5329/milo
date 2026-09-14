-- Session-only demo binding. Remove only the bindings held by this script.
if _G.miloDemoHold then
  for _, bind in ipairs(_G.miloDemoHold.bindings) do bind:unbind() end
end
local state = {bindings = {}, counter = 0, token = nil}
_G.miloDemoHold = state
local function send(method, token)
  hl.dispatch(hl.dsp.exec_cmd('gdbus call --session --dest org.milo.Companion --object-path /org/milo/Companion --method org.milo.Companion.' .. method .. ' ' .. token))
end
state.bindings[1] = hl.bind('SUPER + ALT + SPACE', function()
  if state.token then return end
  state.counter = state.counter + 1
  state.token = tostring(os.time()) .. ':' .. tostring(state.counter)
  send('Press', state.token)
end, {description = 'Milo: hold to talk'})
state.bindings[2] = hl.bind('SUPER + ALT + SPACE', function()
  if not state.token then return end
  local token = state.token
  state.token = nil
  send('Release', token)
end, {release = true, ignore_mods = true, non_consuming = true, transparent = true, description = 'Milo: release to send'})
