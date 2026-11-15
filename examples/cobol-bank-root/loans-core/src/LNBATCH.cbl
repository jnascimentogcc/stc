       IDENTIFICATION DIVISION.
       PROGRAM-ID. LNBATCH.
      *----------------------------------------------------------------
      * NIGHTLY LATE INSTALLMENTS - JOB LNNIGHT
      *----------------------------------------------------------------
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT LATE-RPT ASSIGN TO LATERPT.
       DATA DIVISION.
       FILE SECTION.
       FD  LATE-RPT RECORDING MODE F.
           COPY LATEREC.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
           COPY CONTRREC.
       01  WS-INST-NO              PIC 9(03).
       01  WS-INST-AMT             PIC S9(11)V99 COMP-3.
       01  WS-DAYS                 PIC S9(05)    COMP-3.
       01  WS-MORA                 PIC S9(11)V99 COMP-3.
       01  WS-FINE                 PIC S9(11)V99 COMP-3.
       01  WS-EOF                  PIC X(01) VALUE 'N'.
       01  WS-CNT-DFLT             PIC 9(07) VALUE ZERO.
           EXEC SQL DECLARE C-LATE CURSOR FOR
              SELECT C.CONTR_ID, C.PROD_CD, I.INST_NO, I.INST_AMT,
                     DAYS(CURRENT DATE) - DAYS(I.DUE_DT)
                FROM CONTRACT C, INSTALLMENT I
               WHERE I.CONTR_ID = C.CONTR_ID
                 AND I.PAID_FLG = 'N'
                 AND I.DUE_DT < CURRENT DATE
                 AND C.STATUS IN ('A', 'D')
               ORDER BY C.CONTR_ID, I.INST_NO
           END-EXEC.
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN OUTPUT LATE-RPT
           EXEC SQL OPEN C-LATE END-EXEC
           PERFORM 1000-FETCH
           PERFORM 2000-PROCESS UNTIL WS-EOF = 'Y'
           EXEC SQL CLOSE C-LATE END-EXEC
           CLOSE LATE-RPT
           EXEC SQL COMMIT END-EXEC
           DISPLAY 'LNBATCH NEW DEFAULTS: ' WS-CNT-DFLT
           STOP RUN.
       1000-FETCH.
           EXEC SQL FETCH C-LATE
              INTO :CT-CONTR-ID, :CT-PROD-CD, :WS-INST-NO,
                   :WS-INST-AMT, :WS-DAYS
           END-EXEC
           IF SQLCODE = 100
              MOVE 'Y' TO WS-EOF
           END-IF.
       2000-PROCESS.
           IF WS-DAYS > 3
              PERFORM 2100-CALC-CHRG
              PERFORM 2200-WRITE-RPT
              IF WS-DAYS > 90
                 PERFORM 2300-SET-DFLT
              END-IF
           END-IF
           PERFORM 1000-FETCH.
       2100-CALC-CHRG.
           COMPUTE WS-MORA ROUNDED = WS-INST-AMT * 0.01 / 30 * WS-DAYS
           IF CT-PROD-CD = 'PY'
              MOVE ZERO TO WS-FINE
           ELSE
              COMPUTE WS-FINE ROUNDED = WS-INST-AMT * 0.02
           END-IF.
       2200-WRITE-RPT.
           MOVE CT-CONTR-ID TO LR-CONTR-ID
           MOVE WS-INST-NO  TO LR-INST-NO
           MOVE WS-DAYS     TO LR-DAYS-LATE
           MOVE WS-INST-AMT TO LR-INST-AMT
           MOVE WS-MORA     TO LR-MORA-AMT
           MOVE WS-FINE     TO LR-FINE-AMT
           MOVE CT-PROD-CD  TO LR-PROD-CD
           WRITE LATE-REC.
       2300-SET-DFLT.
           EXEC SQL
              UPDATE CONTRACT
                 SET STATUS = 'D',
                     DAYS_LATE = :WS-DAYS
               WHERE CONTR_ID = :CT-CONTR-ID
                 AND STATUS = 'A'
           END-EXEC
           IF SQLERRD(3) > 0
              ADD 1 TO WS-CNT-DFLT
           END-IF.
