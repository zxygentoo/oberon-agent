MODULE AgentTool;  (*serial agent server for the oberon-agent skill -- Project Oberon 2013 target.*)
  IMPORT SYSTEM, Kernel, FileDir, Files, Modules, Display, Texts, Oberon;

  CONST
    data = -56; stat = -52;            (*RS232 MMIO; status bit0 = rx, bit1 = tx*)
    sync = 0A5H; rsync = 05AH;         (*request / response frame start*)
    opPut = 1; opGet = 2; opCall = 3; opEdit = 4;
    stOk = 0; stNotFound = 1; stError = 3; stNoMatch = 4; stNotUnique = 5;
    editLim = 1024;                    (*max OLD length; oat falls back to GET+PUT beyond -- keep in sync with protocol.rs EDIT_OLD_LIMIT*)

  VAR
    T: Oberon.Task;
    W, PW: Texts.Writer;
    par: Texts.Text;
    parF: Display.Frame;
    name, cmd: ARRAY 64 OF CHAR;
    pfx: ARRAY 32 OF CHAR;
    ownLog: Texts.Text;
    M: INTEGER;                        (*DoEdit pattern length; global because Forward can't see DoEdit's locals*)
    pat, win: ARRAY editLim OF CHAR;   (*DoEdit pattern + sliding match window*)
    dis: ARRAY 256 OF INTEGER;         (*Boyer-Moore displacement table*)

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

  PROCEDURE RecName(VAR s: ARRAY OF CHAR);
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
    Oberon.SetPar(parF, par, 0);                            (*PO 3-arg signature*)
    beg := Oberon.Log.len; Oberon.Call(cmd, res); lim := Oberon.Log.len;  (*PO: Oberon.Call*)
    IF res = 0 THEN status := stOk                          (*PO Modules.res: raw ints*)
    ELSIF (res = 1) OR (res = 5) THEN status := stNotFound  (*1=nofile, 5=nocmd on PO*)
    ELSE status := stError
    END;
    Send(rsync); Send(status); SendInt(lim - beg);
    Texts.OpenReader(R, Oberon.Log, beg);
    WHILE Texts.Pos(R) < lim DO Texts.Read(R, ch); Send(ORD(ch)) END
  END DoCall;

  (*DoEdit: replace a unique occurrence of OLD by NEW inside a file, on the
    device, through the Texts piece list -- unchanged content is never copied
    until Texts.Close streams the result out. The scan is Edit.Search's
    Boyer-Moore, restated to count every non-overlapping match instead of
    stopping at the first.*)

  PROCEDURE Forward(n: INTEGER; VAR R: Texts.Reader);   (*slide win left by n, refill from R*)
    VAR m, j: INTEGER;
  BEGIN m := M - n; j := 0;
    WHILE j < m DO win[j] := win[n + j]; INC(j) END;
    WHILE j < M DO Texts.Read(R, win[j]); INC(j) END
  END Forward;

  PROCEDURE DoEdit;
    VAR T: Texts.Text; R: Texts.Reader; B: Texts.Buffer;
      oldLen, newLen, i, j, n, pos, count, first: INTEGER;
      x: BYTE; done: BOOLEAN;
  BEGIN RecName(name);
    RecInt(oldLen); i := 0;                              (*OLD -> pat; excess drained*)
    WHILE i < oldLen DO Rec(x);
      IF i < editLim THEN pat[i] := CHR(x) END;
      INC(i)
    END;
    RecInt(newLen); i := 0;                              (*NEW -> PW.buf, unbounded*)
    WHILE i < newLen DO Rec(x); Texts.Write(PW, CHR(x)); INC(i) END;
    (*frame fully consumed -- safe to answer*)
    IF (oldLen <= 0) OR (oldLen > editLim) THEN
      Texts.OpenWriter(PW);                              (*drop buffered NEW*)
      Send(rsync); Send(stError); SendInt(0)
    ELSIF Files.Old(name) = NIL THEN
      Texts.OpenWriter(PW);
      Send(rsync); Send(stNotFound); SendInt(0)
    ELSE
      NEW(T); Texts.Open(T, name);                       (*plain or formatted file*)
      M := oldLen; count := 0; first := -1;
      IF M <= T.len THEN
        j := 0; WHILE j < 256 DO dis[j] := M; INC(j) END;
        j := 0; WHILE j < M - 1 DO dis[ORD(pat[j])] := M - 1 - j; INC(j) END;
        Texts.OpenReader(R, T, 0);
        Forward(M, R); pos := M; done := FALSE;
        REPEAT
          j := M;
          REPEAT DEC(j) UNTIL (j < 0) OR (win[j] # pat[j]);
          IF j < 0 THEN INC(count);                      (*match ends at pos*)
            IF first < 0 THEN first := pos - M END;
            n := M                                       (*non-overlapping, like the host counts*)
          ELSE n := dis[ORD(win[M - 1])]
          END;
          IF pos + n <= T.len THEN Forward(n, R); pos := pos + n ELSE done := TRUE END
        UNTIL done
      END;
      IF count = 0 THEN
        Texts.OpenWriter(PW);
        Send(rsync); Send(stNoMatch); SendInt(0)
      ELSIF count > 1 THEN
        Texts.OpenWriter(PW);
        Send(rsync); Send(stNotUnique); SendInt(4); SendInt(count)
      ELSE
        NEW(B); Texts.OpenBuf(B);
        Texts.Delete(T, first, first + M, B);            (*B receives the OLD chars, discarded*)
        Texts.Insert(T, first, PW.buf);                  (*consumes and resets PW.buf*)
        Texts.Close(T, name);                            (*atomic: Files.Register swaps the dir entry*)
        Send(rsync); Send(stOk); SendInt(0)
      END
    END
  END DoEdit;

  PROCEDURE Task;
    VAR op, b: BYTE;
  BEGIN
    IF SYSTEM.BIT(stat, 0) THEN
      Rec(b);
      IF b = sync THEN
        Rec(op);
        IF op = opPut THEN DoPut
        ELSIF op = opGet THEN DoGet
        ELSIF op = opCall THEN DoCall
        ELSIF op = opEdit THEN DoEdit
        END
      END
    END
  END Task;

  (* --- introspection helpers (write to Oberon.Log) --- *)

  PROCEDURE Version*;   (*report the variant to the Log; oat `check` parses it.
    Hardcoded: PO2013 symbol files don't carry string-constant characters, so
    an imported System.Version CONST reads back empty -- and this module is
    variant-specific anyway.*)
  BEGIN Texts.WriteString(W, "Project Oberon 2013"); Texts.WriteLn(W);
    Texts.Append(Oberon.Log, W.buf)
  END Version;

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
      Kernel.GetSector(sec, hp);
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

  PROCEDURE ListModules*;   (*name <TAB> refcnt <TAB> code-addr (PO 'code' field)*)
    VAR m: Modules.Module;
  BEGIN m := Modules.root;
    WHILE m # NIL DO
      IF m.name[0] # 0X THEN
        Texts.WriteString(W, m.name); Texts.Write(W, 9X);
        Texts.WriteInt(W, m.refcnt, 1); Texts.Write(W, 9X);
        Texts.WriteHex(W, m.code); Texts.WriteLn(W)
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
      IF Modules.res = 0 THEN
        Texts.WriteString(W, "loaded "); Texts.WriteString(W, S.s)
      ELSE
        Texts.WriteString(W, "load failed "); Texts.WriteString(W, S.s);
        Texts.WriteString(W, " res="); Texts.WriteInt(W, Modules.res, 1)
      END;
      Texts.WriteLn(W); Texts.Append(Oberon.Log, W.buf)
    END
  END Load;

BEGIN Texts.OpenWriter(W); Texts.OpenWriter(PW); NEW(parF);
  IF Oberon.Log = NIL THEN NEW(ownLog); Texts.Open(ownLog, ""); Oberon.OpenLog(ownLog) END;
  T := Oberon.NewTask(Task, 0); Oberon.Install(T);
  Texts.WriteString(W, "AgentTool installed"); Texts.WriteLn(W); Texts.Append(Oberon.Log, W.buf)
END AgentTool.
