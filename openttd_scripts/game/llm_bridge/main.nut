/* LLMBridge: the controller (Python, admin port) sends {"id":n,"op":"...",...};
 * every answer carries "re":id. GSAdmin.Send drops anything over 1450 bytes of JSON,
 * so lists go out in parts with "part" and a final {"re":id,"done":true}. */
class LLMBridge extends GSController {
	function Start();
	function Handle(cmd);
}

function LLMBridge::Date() {
	local d = GSDate.GetCurrentDate();
	local m = GSDate.GetMonth(d), day = GSDate.GetDayOfMonth(d);
	return GSDate.GetYear(d) + "-" + (m < 10 ? "0" : "") + m + "-" + (day < 10 ? "0" : "") + day;
}

function LLMBridge::Start() {
	GSAdmin.Send({ event = "hello", date = this.Date(), map_x = GSMap.GetMapSizeX(), map_y = GSMap.GetMapSizeY() });
	while (true) {
		while (GSEventController.IsEventWaiting()) {
			local e = GSEventController.GetNextEvent();
			if (e.GetEventType() == GSEvent.ET_ADMIN_PORT) {
				local cmd = GSEventAdminPort.Convert(e).GetObject();
				if (cmd != null) this.Handle(cmd);
			}
		}
		this.Sleep(1);
	}
}

function LLMBridge::Handle(cmd) {
	local id = ("id" in cmd) ? cmd.id : -1;
	local op = ("op" in cmd) ? cmd.op : "";
	if (op == "ping") {
		GSAdmin.Send({ re = id, ok = true, date = this.Date() });
	} else if (op == "info") {
		local companies = [];
		for (local c = GSCompany.COMPANY_FIRST; c <= GSCompany.COMPANY_LAST; c++) {
			if (GSCompany.ResolveCompanyID(c) == GSCompany.COMPANY_INVALID) continue;
			companies.append({ id = c, name = GSCompany.GetName(c), money = GSCompany.GetBankBalance(c) });
		}
		GSAdmin.Send({ re = id, ok = true, date = this.Date(), companies = companies, towns = GSTownList().Count() });
	} else if (op == "towns") {
		local list = GSTownList();
		list.Valuate(GSTown.GetPopulation);
		list.Sort(GSList.SORT_BY_VALUE, GSList.SORT_DESCENDING);
		local part = [], n = 0;
		foreach (t, pop in list) {
			local tile = GSTown.GetLocation(t);
			part.append({ id = t, name = GSTown.GetName(t), pop = pop, x = GSMap.GetTileX(tile), y = GSMap.GetTileY(tile) });
			if (part.len() == 12) { GSAdmin.Send({ re = id, part = n++, towns = part }); part = []; }
		}
		if (part.len() > 0) GSAdmin.Send({ re = id, part = n++, towns = part });
		GSAdmin.Send({ re = id, done = true });
	} else {
		GSAdmin.Send({ re = id, ok = false, error = "unknown op " + op });
	}
}
