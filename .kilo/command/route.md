---
description: Pick a model with the basal router and run the task
---
Pass the task to the `kilo-router_recommend` MCP tool: "$ARGUMENTS".
Present the recommended model (the switch_to and how fields), confidence and the top 3 of the ranking.
If deferred=true, say the router was not sure and recommends the default model.
If the user wants the task executed now, call `kilo-router_run` with model = model_key from the recommendation.
