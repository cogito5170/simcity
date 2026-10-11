/* LLMBridge: the controller (Python, admin port) sends {"id":n,"op":"...",...};
 * every answer carries "re":id. GSAdmin.Send drops anything over 1450 bytes of JSON,
 * so lists go out in parts with "part" and a final {"re":id,"done":true}. */
class LLMBridge extends GSController {
	function Start();
	function Handle(cmd);
	function TileCode(tile);
	function MapRect(id, x0, y0, w, h);
	function UseRoad();
	function BuildPath(id, company, path);
	function StraightRoad(tile);
	function BusStop(id, company, cx, cy);
	function Depot(id, company, cx, cy);
	function PassengerCargo();
	function BuyBus(id, company, depot, stops, count);
	function Vehicles(id, company);
	function Stations(id, company);
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
				if (cmd == null) continue;
				try {
					this.Handle(cmd);
				} catch (err) {   // a bad command must not kill the bridge
					GSAdmin.Send({ re = ("id" in cmd) ? cmd.id : -1, ok = false, error = "script error: " + err });
				}
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
	} else if (op == "map") {
		this.MapRect(id, cmd.x0, cmd.y0, cmd.w, cmd.h);
	} else if (op == "build_path") {
		this.BuildPath(id, cmd.company, cmd.path);
	} else if (op == "bus_stop") {
		this.BusStop(id, cmd.company, cmd.x, cmd.y);
	} else if (op == "depot") {
		this.Depot(id, cmd.company, cmd.x, cmd.y);
	} else if (op == "buy_bus") {
		this.BuyBus(id, cmd.company, cmd.depot, cmd.stops, cmd.count);
	} else if (op == "vehicles") {
		this.Vehicles(id, cmd.company);
	} else if (op == "company") {
		local c = cmd.company;
		local cm = GSCompanyMode(c);   // loan getters read the current company
		GSAdmin.Send({ re = id, ok = true, date = this.Date(), money = GSCompany.GetBankBalance(c),
			loan = GSCompany.GetLoanAmount(), max_loan = GSCompany.GetMaxLoanAmount(),
			value = GSCompany.GetQuarterlyCompanyValue(c, GSCompany.CURRENT_QUARTER) });
	} else if (op == "loan") {
		local cm = GSCompanyMode(cmd.company);
		local ok = GSCompany.SetLoanAmount(cmd.amount);
		GSAdmin.Send({ re = id, ok = ok, error = ok ? "" : GSError.GetLastErrorString(), loan = GSCompany.GetLoanAmount(),
			money = GSCompany.GetBankBalance(cmd.company) });
	} else if (op == "stations") {
		this.Stations(id, cmd.company);
	} else {
		GSAdmin.Send({ re = id, ok = false, error = "unknown op " + op });
	}
}

/* One character per tile, row by row: W water, R road, S road stop, D depot, B buildable flat,
 * b buildable gentle slope, X anything else (houses, industry, steep slope, rail...). */
function LLMBridge::TileCode(tile) {
	if (GSTile.IsWaterTile(tile) || GSTile.IsCoastTile(tile)) return "W";
	if (GSRoad.IsRoadStationTile(tile)) return "S";
	if (GSRoad.IsRoadDepotTile(tile)) return "D";
	if (GSRoad.IsRoadTile(tile)) return "R";
	if (GSTile.IsBuildable(tile)) {
		local s = GSTile.GetSlope(tile);
		if (s == GSTile.SLOPE_FLAT) return "B";
		if (!GSTile.IsSteepSlope(s)) return "b";
	}
	return "X";
}

function LLMBridge::MapRect(id, x0, y0, w, h) {
	local part = 0;
	for (local y = y0; y < y0 + h; y++) {
		local row = "";
		for (local x = x0; x < x0 + w; x++) row += this.TileCode(GSMap.GetTileIndex(x, y));
		GSAdmin.Send({ re = id, part = part++, y = y, row = row });
	}
	GSAdmin.Send({ re = id, done = true });
}

function LLMBridge::UseRoad() {
	local types = GSRoadTypeList(GSRoad.ROADTRAMTYPES_ROAD);
	foreach (rt, _ in types) { if (GSRoad.IsRoadTypeAvailable(rt)) { GSRoad.SetCurrentRoadType(rt); return; } }
}

function LLMBridge::BuildPath(id, company, path) {
	local cm = GSCompanyMode(company);
	this.UseRoad();
	local failed = [];
	for (local i = 0; i + 1 < path.len(); i++) {
		local a = GSMap.GetTileIndex(path[i][0], path[i][1]), b = GSMap.GetTileIndex(path[i + 1][0], path[i + 1][1]);
		if (GSRoad.AreRoadTilesConnected(a, b)) continue;
		if (!GSRoad.BuildRoad(a, b) && GSError.GetLastError() != GSError.ERR_ALREADY_BUILT) {
			failed.append([path[i][0], path[i][1], GSError.GetLastErrorString()]);
			if (failed.len() >= 8) break;
		}
	}
	GSAdmin.Send({ re = id, ok = failed.len() == 0, failed = failed, money = GSCompany.GetBankBalance(company) });
}

/* Straight road piece along one axis only (no junction): a drive-through stop fits there. */
function LLMBridge::StraightRoad(tile) {
	if (!GSRoad.IsRoadTile(tile) || GSRoad.IsRoadStationTile(tile) || GSRoad.IsRoadDepotTile(tile)) return null;
	if (GSTile.GetSlope(tile) != GSTile.SLOPE_FLAT) return null;
	local dx = GSMap.GetTileIndex(1, 0), dy = GSMap.GetTileIndex(0, 1);
	local ex = GSRoad.AreRoadTilesConnected(tile, tile + dx) || GSRoad.AreRoadTilesConnected(tile, tile - dx);
	local ey = GSRoad.AreRoadTilesConnected(tile, tile + dy) || GSRoad.AreRoadTilesConnected(tile, tile - dy);
	if (ex && !ey) return tile + dx;
	if (ey && !ex) return tile + dy;
	return null;
}

function LLMBridge::BusStop(id, company, cx, cy) {
	local cm = GSCompanyMode(company);
	this.UseRoad();
	for (local r = 0; r <= 8; r++) {
		for (local dy = -r; dy <= r; dy++) for (local dx = -r; dx <= r; dx++) {
			if (abs(dx) != r && abs(dy) != r) continue;
			local tile = GSMap.GetTileIndex(cx + dx, cy + dy);
			if (!GSMap.IsValidTile(tile)) continue;
			local front = this.StraightRoad(tile);
			if (front == null) continue;
			if (GSRoad.BuildDriveThroughRoadStation(tile, front, GSRoad.ROADVEHTYPE_BUS, GSStation.STATION_NEW)) {
				GSAdmin.Send({ re = id, ok = true, x = cx + dx, y = cy + dy, station = GSStation.GetStationID(tile) });
				return;
			}
		}
	}
	GSAdmin.Send({ re = id, ok = false, error = "no straight road for a stop near " + cx + "," + cy });
}

function LLMBridge::Depot(id, company, cx, cy) {
	local cm = GSCompanyMode(company);
	this.UseRoad();
	local dirs = [GSMap.GetTileIndex(1, 0), GSMap.GetTileIndex(0, 1)];
	for (local r = 1; r <= 8; r++) {
		for (local dy = -r; dy <= r; dy++) for (local dx = -r; dx <= r; dx++) {
			if (abs(dx) != r && abs(dy) != r) continue;
			local tile = GSMap.GetTileIndex(cx + dx, cy + dy);
			if (!GSMap.IsValidTile(tile) || !GSTile.IsBuildable(tile) || GSTile.GetSlope(tile) != GSTile.SLOPE_FLAT) continue;
			foreach (d in dirs) foreach (front in [tile + d, tile - d]) {
				if (!GSRoad.IsRoadTile(front) || GSRoad.IsRoadStationTile(front) || GSRoad.IsRoadDepotTile(front)) continue;
				if (GSRoad.BuildRoadDepot(tile, front)) {
					GSRoad.BuildRoad(tile, front);
					GSAdmin.Send({ re = id, ok = true, x = cx + dx, y = cy + dy });
					return;
				}
			}
		}
	}
	GSAdmin.Send({ re = id, ok = false, error = "no depot spot near " + cx + "," + cy });
}

function LLMBridge::PassengerCargo() {
	foreach (c, _ in GSCargoList()) if (GSCargo.GetTownEffect(c) == GSCargo.TE_PASSENGERS) return c;
	return -1;
}

function LLMBridge::BuyBus(id, company, depot, stops, count) {
	local cm = GSCompanyMode(company);
	local pax = this.PassengerCargo();
	local engines = GSEngineList(GSVehicle.VT_ROAD);
	engines.Valuate(GSEngine.IsBuildable); engines.KeepValue(1);
	engines.Valuate(GSEngine.GetCargoType); engines.KeepValue(pax);
	engines.Valuate(GSEngine.GetMaxSpeed); engines.Sort(GSList.SORT_BY_VALUE, GSList.SORT_DESCENDING);
	if (engines.IsEmpty()) { GSAdmin.Send({ re = id, ok = false, error = "no buildable bus" }); return; }
	local engine = engines.Begin();
	local dtile = GSMap.GetTileIndex(depot[0], depot[1]);
	local bought = [];
	for (local i = 0; i < count; i++) {
		local v = GSVehicle.BuildVehicle(dtile, engine);
		if (!GSVehicle.IsValidVehicle(v)) { GSAdmin.Send({ re = id, ok = false, error = GSError.GetLastErrorString(), bought = bought }); return; }
		foreach (s in stops) GSOrder.AppendOrder(v, GSMap.GetTileIndex(s[0], s[1]), GSOrder.OF_NONE);
		if (i % 2 == 1 && stops.len() > 1) GSOrder.SkipToOrder(v, 1);   // spread buses over both directions
		GSVehicle.StartStopVehicle(v);
		bought.append(v);
	}
	GSAdmin.Send({ re = id, ok = true, engine = GSEngine.GetName(engine), bought = bought, money = GSCompany.GetBankBalance(company) });
}

function LLMBridge::Vehicles(id, company) {
	local list = GSVehicleList();
	local part = [], n = 0;
	foreach (v, _ in list) {
		if (GSVehicle.GetOwner(v) != company) continue;
		local t = GSVehicle.GetLocation(v);
		part.append({ id = v, x = GSMap.GetTileX(t), y = GSMap.GetTileY(t), state = GSVehicle.GetState(v),
			profit = GSVehicle.GetProfitThisYear(v), last = GSVehicle.GetProfitLastYear(v), age = GSVehicle.GetAge(v) });
		if (part.len() == 10) { GSAdmin.Send({ re = id, part = n++, vehicles = part }); part = []; }
	}
	if (part.len() > 0) GSAdmin.Send({ re = id, part = n++, vehicles = part });
	GSAdmin.Send({ re = id, done = true });
}


function LLMBridge::Stations(id, company) {
	local cm = GSCompanyMode(company);
	local pax = this.PassengerCargo();
	local part = [], n = 0;
	foreach (st, _ in GSStationList(GSStation.STATION_ANY)) {
		local t = GSStation.GetLocation(st);
		part.append({ id = st, name = GSStation.GetName(st), x = GSMap.GetTileX(t), y = GSMap.GetTileY(t),
			waiting = GSStation.GetCargoWaiting(st, pax),
			rating = GSStation.HasCargoRating(st, pax) ? GSStation.GetCargoRating(st, pax) : -1 });
		if (part.len() == 10) { GSAdmin.Send({ re = id, part = n++, stations = part }); part = []; }
	}
	if (part.len() > 0) GSAdmin.Send({ re = id, part = n++, stations = part });
	GSAdmin.Send({ re = id, done = true });
}
