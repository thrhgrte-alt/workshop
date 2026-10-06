--!strict
local started = tick()
task.wait(1)
print("elapsed", tick() - started)
