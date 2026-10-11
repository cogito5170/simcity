class LLMBridgeInfo extends GSInfo {
	function GetAuthor()      { return "simcity AI mayor"; }
	function GetName()        { return "LLMBridge"; }
	function GetShortName()   { return "LLMB"; }
	function GetDescription() { return "Runs JSON commands from the admin port on behalf of one company and reports back"; }
	function GetVersion()     { return 1; }
	function GetDate()        { return "2026-10-11"; }
	function CreateInstance() { return "LLMBridge"; }
	function GetAPIVersion()  { return "15"; }
}
RegisterGS(LLMBridgeInfo());
