# Next Steps

## Mid-Call Transfer Handling

### Problem
When insurance rep transfers bot to different department mid-call, the bot encounters a new IVR system or human. Currently there is no mechanism to re-enter IVR detection mode after initial call connection.

### Scenario
1. Bot in verification node (main_llm active)
2. Rep says "Let me transfer you to prior auth department"
3. Bot hears hold music / new IVR menu / new human agent
4. Need to detect: IVR system vs human (should use classifier_llm)
5. Resume appropriate flow based on detection

### Implementation Considerations

**Option 1: IVRNavigator Continuous Monitoring**
- Investigate if IVRNavigator can continuously monitor for IVR patterns
- May require keeping IVRNavigator active throughout call, not just at start

**Option 2: Explicit Transfer Detection Function**
- Add `handle_department_transfer()` function to verification node
- LLM detects transfer language ("transferring you", "let me connect you")
- Function switches to classifier_llm and resets IVRNavigator state
- Re-enters IVR detection mode

**Option 3: Audio Pattern Detection**
- Detect hold music or silence patterns in audio stream
- Automatically trigger IVR re-detection mode
- Switch to classifier_llm when transfer detected

### Questions to Answer
- Does IVRNavigator only run during StartFrame or continuously?
- Can IVRNavigator state be reset mid-call?
- What audio signals indicate a department transfer?
