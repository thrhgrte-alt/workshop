--!strict
local part = workspace:WaitForChild("Ball") :: BasePart
local mover = Instance.new("BodyVelocity")
mover.Velocity = Vector3.new(0, 50, 0)
mover.MaxForce = Vector3.new(0, 1e5, 0)
mover.Parent = part
