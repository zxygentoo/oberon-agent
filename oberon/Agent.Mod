MODULE Agent;  (*puck serial agent server -- Extended Oberon target. See spec section 4.4.*)
  IMPORT SYSTEM, Disk, FileDir, Files, Modules, Display, Texts, Oberon;

  CONST
    data = -56; stat = -52;            (*RS232 MMIO; status bit0 = rx, bit1 = tx*)
    sync = 0A5H; rsync = 05AH;         (*request / response frame start*)
    opPut = 1; opGet = 2; opCall = 3;
    stOk = 0; stNotFound = 1; stError = 3;

  VAR
    T: Oberon.Task;
    W, PW: Texts.Writer;               (*W: Log/output writer;  PW: builds CALL par-text*)
    par: Texts.Text;
    parF: Display.Frame;               (*dummy frame for Oberon.SetPar; cmd/pos start at 0*)
    name, cmd: ARRAY 64 OF CHAR;
    pfx: ARRAY 32 OF CHAR;
    ownLog: Texts.Text;                (*installed as Oberon.Log if none exists yet at boot*)

  (* --- serial byte layer (PCLink1's Rec/Send, status-gated) --- *)

  PROCEDURE Rec(VAR x: BYTE);
  BEGIN REPEAT UNTIL SYSTEM.BIT(stat, 0); SYSTEM.GET(data, x)
  END Rec;

  PROCEDURE Send(x: BYTE);
  BEGIN REPEAT UNTIL SYSTEM.BIT(stat, 1); SYSTEM.PUT(data, x)
  END Send;

  PROCEDURE RecInt(VAR n: INTEGER);    (*4 bytes, little-endian*)
    VAR a, b, c, d: BYTE;
  BEGIN Rec(a); Rec(b); Rec(c); Rec(d);
    n := a + b*100H + c*10000H + d*1000000H
  END RecInt;

  PROCEDURE SendInt(n: INTEGER);
  BEGIN Send(n MOD 100H); Send(n DIV 100H MOD 100H);
    Send(n DIV 10000H MOD 100H); Send(n DIV 1000000H MOD 100H)
  END SendInt;

  PROCEDURE RecName(VAR s: ARRAY OF CHAR);   (*1-byte length prefix, then the chars*)
    VAR n, i: INTEGER; x: BYTE;
  BEGIN Rec(x); n := x; i := 0;
    WHILE i < n DO Rec(x); s[i] := CHR(x); INC(i) END;
    s[i] := 0X
  END RecName;

  (* --- request handlers --- *)

  PROCEDURE DoPut;
    VAR F: Files.File; R: Files.Rider; len, i: INTEGER; x: BYTE;
  BEGIN RecName(name); RecInt(len);
    F := Files.New(name); Files.Set(R, F, 0); i := 0;
    WHILE i < len DO Rec(x); Files.WriteByte(R, x); INC(i) END;
    Files.Register(F);
    Send(rsync); Send(stOk); SendInt(0)
  END DoPut;

  PROCEDURE DoGet;
    VAR F: Files.File; R: Files.Rider; len, i: INTEGER; x: BYTE;
  BEGIN RecName(name); F := Files.Old(name);
    IF F = NIL THEN
      Send(rsync); Send(stNotFound); SendInt(0)
    ELSE
      len := Files.Length(F); Files.Set(R, F, 0);
      Send(rsync); Send(stOk); SendInt(len); i := 0;
      WHILE i < len DO Files.ReadByte(R, x); Send(x); INC(i) END
    END
  END DoGet;

  PROCEDURE DoCall;
    VAR len, i, res, status, beg, lim: INTEGER; x: BYTE; ch: CHAR; R: Texts.Reader;
  BEGIN RecName(cmd); RecInt(len);
    NEW(par); Texts.Open(par, ""); i := 0;
    WHILE i < len DO Rec(x); Texts.Write(PW, CHR(x)); INC(i) END;
    Texts.Append(par, PW.buf);
    Oberon.SetPar(parF, par, 0, 0, 0);     (*F, T, cmd, pos, res -- params start at 0*)
    beg := Oberon.Log.len; Modules.Call(cmd, res); lim := Oberon.Log.len;
    IF res = Modules.noerr THEN status := stOk
    ELSIF (res = Modules.nofile) OR (res = Modules.nocmd)
       OR (res = Modules.badcmd) OR (res = Modules.nomod) THEN status := stNotFound
    ELSE status := stError
    END;
    Send(rsync); Send(status); SendInt(lim - beg);
    Texts.OpenReader(R, Oberon.Log, beg);
    WHILE Texts.Pos(R) < lim DO Texts.Read(R, ch); Send(ORD(ch)) END
  END DoCall;

  PROCEDURE Task;   (*one poll tick; Oberon.Loop calls this when installed*)
    VAR op, b: BYTE;
  BEGIN
    IF SYSTEM.BIT(stat, 0) THEN
      Rec(b);                          (*sync; a non-sync byte is dropped to resync*)
      IF b = sync THEN
        Rec(op);
        IF op = opPut THEN DoPut
        ELSIF op = opGet THEN DoGet
        ELSIF op = opCall THEN DoCall
        END
      END
    END
  END Task;

  (* --- introspection helpers (write to Oberon.Log, unlike viewer-coupled originals) --- *)

  PROCEDURE Prefixed(p, s: ARRAY OF CHAR): BOOLEAN;
    VAR i: INTEGER;
  BEGIN i := 0;
    WHILE (p[i] # 0X) & (p[i] = s[i]) DO INC(i) END;
    RETURN p[i] = 0X
  END Prefixed;

  PROCEDURE FileEntry(fn: FileDir.FileName; sec: INTEGER; VAR continue: BOOLEAN);
    VAR hp: FileDir.FileHeader;
  BEGIN
    IF Prefixed(pfx, fn) THEN
      Disk.GetSector(sec, hp);
      Texts.WriteString(W, fn); Texts.Write(W, 9X);
      Texts.WriteInt(W, hp.aleng*FileDir.SectorSize + hp.bleng - FileDir.HeaderSize, 1);
      Texts.Write(W, 9X); Texts.WriteClock(W, hp.date); Texts.WriteLn(W)
    END;
    continue := TRUE
  END FileEntry;

  PROCEDURE ListFiles*;
    VAR S: Texts.Scanner;
  BEGIN
    Texts.OpenScanner(S, Oberon.Par.text, Oberon.Par.pos); Texts.Scan(S);
    IF S.class = Texts.Name THEN pfx := S.s ELSE pfx[0] := 0X END;
    FileDir.Enumerate(pfx, FileEntry);
    Texts.Append(Oberon.Log, W.buf)
  END ListFiles;

  PROCEDURE ListModules*;   (*name <TAB> refcnt <TAB> code-addr (EO 'prg')*)
    VAR m: Modules.Module;
  BEGIN m := Modules.root;
    WHILE m # NIL DO
      IF m.name[0] # 0X THEN
        Texts.WriteString(W, m.name); Texts.Write(W, 9X);
        Texts.WriteInt(W, m.refcnt, 1); Texts.Write(W, 9X);
        Texts.WriteHex(W, m.prg); Texts.WriteLn(W)
      END;
      m := m.next
    END;
    Texts.Append(Oberon.Log, W.buf)
  END ListModules;

  PROCEDURE Load*;
    VAR S: Texts.Scanner; m: Modules.Module;
  BEGIN
    Texts.OpenScanner(S, Oberon.Par.text, Oberon.Par.pos); Texts.Scan(S);
    IF S.class = Texts.Name THEN
      Modules.Load(S.s, m);
      IF Modules.res = Modules.noerr THEN
        Texts.WriteString(W, "loaded "); Texts.WriteString(W, S.s)
      ELSE
        Texts.WriteString(W, "load failed "); Texts.WriteString(W, S.s);
        Texts.WriteString(W, " res="); Texts.WriteInt(W, Modules.res, 1)
      END;
      Texts.WriteLn(W); Texts.Append(Oberon.Log, W.buf)
    END
  END Load;

  PROCEDURE Run*;
  BEGIN Oberon.Install(T);
    Texts.WriteString(W, "Agent started"); Texts.WriteLn(W); Texts.Append(Oberon.Log, W.buf)
  END Run;

  PROCEDURE Stop*;
  BEGIN Oberon.Remove(T);
    Texts.WriteString(W, "Agent stopped"); Texts.WriteLn(W); Texts.Append(Oberon.Log, W.buf)
  END Stop;

BEGIN Texts.OpenWriter(W); Texts.OpenWriter(PW); NEW(parF);
  IF Oberon.Log = NIL THEN NEW(ownLog); Texts.Open(ownLog, ""); Oberon.OpenLog(ownLog) END;
  T := Oberon.NewTask(Task, 0); Run   (*auto-start at boot (Oberon.Mod's body loads Agent)*)
END Agent.
