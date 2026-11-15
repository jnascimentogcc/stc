       IDENTIFICATION DIVISION.
       PROGRAM-ID. TLRQRY.
      *----------------------------------------------------------------
      * BALANCE ENQUIRY - CICS TRANSACTION TLRQ
      *----------------------------------------------------------------
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
       01  WS-ACCT                 PIC 9(10).
       01  WS-BALANCE              PIC S9(9)V99 COMP-3.
       LINKAGE SECTION.
       01  DFHCOMMAREA             PIC X(1).
       PROCEDURE DIVISION.
       0000-MAIN.
           EXEC CICS RECEIVE MAP('TLRQRY') MAPSET('TLRSET')
                END-EXEC
           PERFORM 1000-READ-BALANCE
           EXEC CICS SEND MAP('TLRQRY') MAPSET('TLRSET') ERASE
                END-EXEC
           EXEC CICS RETURN END-EXEC.
       1000-READ-BALANCE.
           EXEC SQL
              SELECT BALANCE
                INTO :WS-BALANCE
                FROM ACCOUNT
               WHERE ACCT_ID = :WS-ACCT
           END-EXEC.
