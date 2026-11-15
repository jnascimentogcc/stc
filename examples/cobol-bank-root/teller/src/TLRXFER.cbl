       IDENTIFICATION DIVISION.
       PROGRAM-ID. TLRXFER.
      *----------------------------------------------------------------
      * TELLER TRANSFER ENTRY - TERMINAL SCREEN
      * A teller keys a transfer between two accounts. The amount must
      * be positive and no more than 5,000.00 per transfer, and the
      * source account needs enough balance.
      *----------------------------------------------------------------
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
       01  WS-FROM-ACCT            PIC 9(10).
       01  WS-TO-ACCT              PIC 9(10).
       01  WS-AMOUNT               PIC 9(7)V99.
       01  WS-PIN                  PIC X(4).
       01  WS-BALANCE              PIC S9(9)V99 COMP-3.
       01  WS-MSG                  PIC X(40) VALUE SPACES.
       01  WS-MAX-TRANSFER         PIC 9(7)V99 VALUE 5000.00.
       SCREEN SECTION.
       01  TRANSFER-SCREEN.
           05  BLANK SCREEN.
           05  LINE 2 COL 25 VALUE "TRANSFER BETWEEN ACCOUNTS".
           05  LINE 4 COL 5 VALUE "From account:".
           05  SC-FROM LINE 4 COL 22 PIC 9(10) USING WS-FROM-ACCT
                   REQUIRED.
           05  LINE 5 COL 5 VALUE "To account:".
           05  SC-TO LINE 5 COL 22 PIC 9(10) USING WS-TO-ACCT
                   REQUIRED.
           05  LINE 6 COL 5 VALUE "Amount:".
           05  SC-AMOUNT LINE 6 COL 22 PIC 9(7)V99 USING WS-AMOUNT
                   REQUIRED.
           05  LINE 7 COL 5 VALUE "Teller PIN:".
           05  SC-PIN LINE 7 COL 22 PIC X(4) USING WS-PIN SECURE.
           05  SC-MSG LINE 10 COL 5 PIC X(40) FROM WS-MSG.
       PROCEDURE DIVISION.
       0000-MAIN.
           PERFORM 1000-ENTER-TRANSFER
           PERFORM 2000-VALIDATE
           IF WS-MSG = SPACES
              PERFORM 3000-POST-TRANSFER
           END-IF
           DISPLAY TRANSFER-SCREEN
           STOP RUN.
       1000-ENTER-TRANSFER.
           DISPLAY TRANSFER-SCREEN
           ACCEPT TRANSFER-SCREEN.
       2000-VALIDATE.
           IF WS-AMOUNT NOT > 0
              MOVE 'AMOUNT MUST BE POSITIVE' TO WS-MSG
           END-IF
           IF WS-AMOUNT > WS-MAX-TRANSFER
              MOVE 'ABOVE THE 5,000.00 LIMIT' TO WS-MSG
           END-IF
           IF WS-FROM-ACCT = WS-TO-ACCT
              MOVE 'ACCOUNTS MUST DIFFER' TO WS-MSG
           END-IF
           IF WS-MSG = SPACES
              EXEC SQL
                 SELECT BALANCE
                   INTO :WS-BALANCE
                   FROM ACCOUNT
                  WHERE ACCT_ID = :WS-FROM-ACCT
              END-EXEC
              IF SQLCODE NOT = 0
                 MOVE 'SOURCE ACCOUNT NOT FOUND' TO WS-MSG
              ELSE
                 IF WS-BALANCE < WS-AMOUNT
                    MOVE 'INSUFFICIENT BALANCE' TO WS-MSG
                 END-IF
              END-IF
           END-IF.
       3000-POST-TRANSFER.
           EXEC SQL
              UPDATE ACCOUNT SET BALANCE = BALANCE - :WS-AMOUNT
               WHERE ACCT_ID = :WS-FROM-ACCT
           END-EXEC
           EXEC SQL
              UPDATE ACCOUNT SET BALANCE = BALANCE + :WS-AMOUNT
               WHERE ACCT_ID = :WS-TO-ACCT
           END-EXEC
           EXEC SQL
              INSERT INTO TRANSFER (FROM_ACCT, TO_ACCT, AMOUNT, TRF_TS)
              VALUES (:WS-FROM-ACCT, :WS-TO-ACCT, :WS-AMOUNT,
                      CURRENT TIMESTAMP)
           END-EXEC
           MOVE 'TRANSFER DONE' TO WS-MSG.
