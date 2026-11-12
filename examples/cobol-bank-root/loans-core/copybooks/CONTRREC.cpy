      *----------------------------------------------------------------
      * CONTRREC - LOAN CONTRACT
      *----------------------------------------------------------------
       01  CONTRACT-REC.
           05  CT-CONTR-ID         PIC 9(10).
           05  CT-CUST-ID          PIC 9(10).
           05  CT-PROD-CD          PIC X(02).
               88  CT-PROD-PERS            VALUE 'PL'.
               88  CT-PROD-PAYR            VALUE 'PY'.
               88  CT-PROD-MORT            VALUE 'MT'.
           05  CT-PRINC-AMT        PIC S9(11)V99 COMP-3.
           05  CT-RATE-MO          PIC 9V9(6)    COMP-3.
           05  CT-TERM-MO          PIC 9(03).
           05  CT-AMORT-TP         PIC X(01).
               88  CT-AMORT-PRICE          VALUE 'P'.
               88  CT-AMORT-SAC            VALUE 'S'.
           05  CT-START-DT         PIC 9(08).
           05  CT-STATUS           PIC X(01).
               88  CT-ST-ACTIVE            VALUE 'A'.
               88  CT-ST-DEFAULT           VALUE 'D'.
               88  CT-ST-CLOSED            VALUE 'C'.
           05  CT-DAYS-LATE        PIC 9(05).
