class IdleAI extends AIController {
	function Start() {
		AICompany.SetName("Claude Transport");
		while (true) this.Sleep(1000);
	}
}
