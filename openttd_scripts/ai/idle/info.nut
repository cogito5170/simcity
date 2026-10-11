class IdleAIInfo extends AIInfo {
	function GetAuthor()      { return "simcity AI mayor"; }
	function GetName()        { return "IdleAI"; }
	function GetShortName()   { return "IDLE"; }
	function GetDescription() { return "Does nothing; holds the company that LLMBridge builds for"; }
	function GetVersion()     { return 1; }
	function GetDate()        { return "2026-10-11"; }
	function CreateInstance() { return "IdleAI"; }
	function GetAPIVersion()  { return "15"; }
	function UseAsRandomAI()  { return false; }
}
RegisterAI(IdleAIInfo());
