-- A live run's activity (model/calling/tools phase, current tool, heartbeat)
-- gets its own small column. In request.agent it made every phase change
-- rewrite the whole request, which for subagents still carries their
-- display seed (hundreds of KB). INSTANT; readers fall back to
-- request.agent.activity while it is NULL.
ALTER TABLE chat_runs
  ADD COLUMN activity TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_nopad_bin DEFAULT NULL,
  ALGORITHM=INSTANT;
